"""Invariant tests: guard the null-check state machine and collector content constraints.

These tests protect the core architectural invariant: NULL checks on SilverContent
drive the processing pipeline. If a collector accidentally writes to SilverContent.text,
rows fall out of the download/extract/transcription pipeline.

State machine:
    text=NULL, error=NULL, fetched_at=NULL  -> needs download
    text=NULL, error=NULL, fetched_at=SET   -> downloaded, needs extraction
    text=SET                                -> completed (skipped by all pipelines)
    error=SET                               -> failed (skipped by all pipelines)

For SilverObservation:
    comments_json=NULL, error=NULL          -> needs comments (reddit, hackernews, lobsters)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.db import SilverContent, SilverObservation
from tests.factories import (
    hf_paper,
    hn_hit,
    hn_search_response,
    lobsters_story,
    make_config,
    reddit_listing,
    reddit_post,
    rss_entry,
    rss_feed,
    seed_content,
    seed_observation,
    youtube_entry,
)
from tests.helpers import collect

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Null-check state machine queries
# ---------------------------------------------------------------------------


class TestNullCheckStateTransitions:
    """Verify the null-check state machine queries find/exclude rows correctly."""

    def test_pending_content_found_by_download_query(self, engine):
        """text=NULL, error=NULL, fetched_at=NULL -> found by download query."""
        seed_content(engine, "https://example.com/pending", domain="example.com")

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.is_(None),
                )
            ).fetchall()
            assert len(rows) == 1

    def test_pending_content_excluded_from_extract_query(self, engine):
        """text=NULL, error=NULL, fetched_at=NULL -> NOT found by extract query."""
        seed_content(engine, "https://example.com/pending", domain="example.com")

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.isnot(None),
                )
            ).fetchall()
            assert len(rows) == 0

    def test_downloaded_content_found_by_extract_query(self, engine):
        """text=NULL, error=NULL, fetched_at=SET -> found by extract query."""
        seed_content(
            engine,
            "https://example.com/downloaded",
            domain="example.com",
            fetched_at="2024-01-01T00:00:00Z",
        )

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.isnot(None),
                )
            ).fetchall()
            assert len(rows) == 1

    def test_downloaded_content_excluded_from_download_query(self, engine):
        """text=NULL, error=NULL, fetched_at=SET -> NOT found by download query."""
        seed_content(
            engine,
            "https://example.com/downloaded",
            domain="example.com",
            fetched_at="2024-01-01T00:00:00Z",
        )

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.is_(None),
                )
            ).fetchall()
            assert len(rows) == 0

    def test_completed_content_excluded_from_all_queries(self, engine):
        """text=SET -> excluded from download AND extract queries."""
        seed_content(
            engine,
            "https://example.com/done",
            domain="example.com",
            text="some text",
            fetched_at="2024-01-01T00:00:00Z",
        )

        with engine.connect() as conn:
            download = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.is_(None),
                )
            ).fetchall()
            extract = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.isnot(None),
                )
            ).fetchall()
            assert len(download) == 0
            assert len(extract) == 0

    def test_failed_content_excluded_from_all_queries(self, engine):
        """error=SET -> excluded from download AND extract queries."""
        seed_content(
            engine,
            "https://example.com/failed",
            domain="example.com",
            error="some error",
        )

        with engine.connect() as conn:
            download = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.is_(None),
                )
            ).fetchall()
            extract = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.isnot(None),
                )
            ).fetchall()
            assert len(download) == 0
            assert len(extract) == 0

    def test_failed_with_text_excluded_from_all_queries(self, engine):
        """text=SET AND error=SET -> excluded from all processing queries."""
        seed_content(
            engine,
            "https://example.com/partial",
            domain="example.com",
            text="partial",
            error="parse error",
            fetched_at="2024-01-01T00:00:00Z",
        )

        with engine.connect() as conn:
            download = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.is_(None),
                )
            ).fetchall()
            extract = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.isnot(None),
                )
            ).fetchall()
            assert len(download) == 0
            assert len(extract) == 0

    def test_youtube_included_in_transcription_query(self, engine):
        """YouTube content (text=NULL, error=NULL) with youtube observation -> found by transcription query."""
        cid = seed_content(engine, "https://youtube.com/watch?v=abc", domain="youtube.com")
        seed_observation(engine, source_type="youtube", external_id="abc", content_id=cid)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent.id)
                .join(SilverObservation, SilverObservation.content_id == SilverContent.id)
                .where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverObservation.source_type == "youtube",
                )
            ).fetchall()
            assert len(rows) == 1

    def test_non_youtube_excluded_from_transcription_query(self, engine):
        """Non-YouTube content -> NOT found by transcription query."""
        cid = seed_content(engine, "https://example.com/article", domain="example.com")
        seed_observation(engine, source_type="hackernews", external_id="12345", content_id=cid)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent.id)
                .join(SilverObservation, SilverObservation.content_id == SilverContent.id)
                .where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverObservation.source_type == "youtube",
                )
            ).fetchall()
            assert len(rows) == 0

    def test_completed_youtube_excluded_from_transcription_query(self, engine):
        """YouTube content with text=SET -> NOT found by transcription query."""
        cid = seed_content(
            engine,
            "https://youtube.com/watch?v=done",
            domain="youtube.com",
            text="transcript here",
        )
        seed_observation(engine, source_type="youtube", external_id="done", content_id=cid)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent.id)
                .join(SilverObservation, SilverObservation.content_id == SilverContent.id)
                .where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverObservation.source_type == "youtube",
                )
            ).fetchall()
            assert len(rows) == 0

    def test_pending_comments_found_by_comments_query(self, engine):
        """comments_json=NULL, error=NULL, source_type in list -> found."""
        seed_observation(engine, source_type="reddit", external_id="abc123")

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverObservation).where(
                    SilverObservation.comments_json.is_(None),
                    SilverObservation.error.is_(None),
                    SilverObservation.source_type.in_(["reddit", "hackernews", "lobsters"]),
                )
            ).fetchall()
            assert len(rows) == 1

    def test_completed_comments_excluded_from_comments_query(self, engine):
        """comments_json=SET -> excluded from comments query."""
        seed_observation(
            engine,
            source_type="reddit",
            external_id="done456",
            comments_json='[{"body": "hi"}]',
        )

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverObservation).where(
                    SilverObservation.comments_json.is_(None),
                    SilverObservation.error.is_(None),
                    SilverObservation.source_type.in_(["reddit", "hackernews", "lobsters"]),
                )
            ).fetchall()
            assert len(rows) == 0

    def test_failed_comments_excluded_from_comments_query(self, engine):
        """error=SET on observation -> excluded from comments query."""
        seed_observation(
            engine,
            source_type="hackernews",
            external_id="err789",
            error="rate limited",
        )

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverObservation).where(
                    SilverObservation.comments_json.is_(None),
                    SilverObservation.error.is_(None),
                    SilverObservation.source_type.in_(["reddit", "hackernews", "lobsters"]),
                )
            ).fetchall()
            assert len(rows) == 0

    def test_enrichment_finds_enriched_at_null(self, engine):
        """enriched_at=NULL -> found by enrichment query."""
        seed_content(engine, "https://example.com/to-enrich", domain="example.com")

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).where(SilverContent.enriched_at.is_(None))).fetchall()
            assert len(rows) == 1

    def test_enrichment_excludes_already_enriched(self, engine):
        """enriched_at=SET -> excluded from enrichment query."""
        seed_content(
            engine,
            "https://example.com/enriched",
            domain="example.com",
            text="hello",
            enriched_at="2024-06-01T00:00:00Z",
        )

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).where(SilverContent.enriched_at.is_(None))).fetchall()
            assert len(rows) == 0

    def test_mixed_states_correctly_routed(self, engine):
        """Multiple rows in different states are each picked up by the right query."""
        # Pending download
        seed_content(engine, "https://example.com/pending", domain="example.com")
        # Downloaded, awaiting extraction
        seed_content(
            engine,
            "https://example.com/downloaded",
            domain="example.com",
            fetched_at="2024-01-01T00:00:00Z",
        )
        # Completed
        seed_content(
            engine,
            "https://example.com/done",
            domain="example.com",
            text="done",
            fetched_at="2024-01-01T00:00:00Z",
        )
        # Failed
        seed_content(
            engine,
            "https://example.com/failed",
            domain="example.com",
            error="timeout",
        )

        with engine.connect() as conn:
            download = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.is_(None),
                )
            ).fetchall()
            extract = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.isnot(None),
                )
            ).fetchall()

            assert len(download) == 1
            assert download[0].canonical_url == "https://example.com/pending"

            assert len(extract) == 1
            assert extract[0].canonical_url == "https://example.com/downloaded"


# ---------------------------------------------------------------------------
# Collector content constraints: collectors must NOT set SilverContent.text
# (except for self-posts via _ensure_self_post_content)
# ---------------------------------------------------------------------------


class TestCollectorContentConstraints:
    """Verify collectors do NOT write to SilverContent.text (would break null-check pipeline).

    Self-posts are the exception: they write text via _ensure_self_post_content because
    there is no external URL to download.
    """

    def test_hackernews_external_does_not_set_text(self, engine, mock_http, log):
        """HN collector with external URL: SilverContent.text must remain NULL."""
        from aggre.collectors.hackernews.collector import HackernewsCollector
        from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hn_hit()),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource()]))
            collect(HackernewsCollector(), engine, config.hackernews, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "HN collector must not set SilverContent.text for external URLs"

    def test_hackernews_self_post_sets_text(self, engine, mock_http, log):
        """Ask HN self-post: allowed to set SilverContent.text via _ensure_self_post_content."""
        from aggre.collectors.hackernews.collector import HackernewsCollector
        from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource

        hit = hn_hit(object_id="999")
        hit["url"] = None
        hit["story_text"] = "Self-post content"
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource()]))
            collect(HackernewsCollector(), engine, config.hackernews, config.settings, log)

        with engine.connect() as conn:
            sc = conn.execute(sa.select(SilverContent)).fetchone()
            assert sc is not None and sc.text == "Self-post content"

    def test_reddit_link_post_does_not_set_text(self, engine, mock_http, log):
        """Reddit link post: SilverContent.text must remain NULL on the linked content."""
        from aggre.collectors.reddit.collector import RedditCollector
        from aggre.collectors.reddit.config import RedditConfig, RedditSource

        post = reddit_post(
            post_id="link1",
            url="https://example.com/linked-article",
            is_self=False,
        )
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.canonical_url.like("%example.com%"),
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "Reddit collector must not set SilverContent.text for link posts"

    def test_reddit_self_post_sets_text(self, engine, mock_http, log):
        """Reddit self-post: allowed to set SilverContent.text via _ensure_self_post_content."""
        from aggre.collectors.reddit.collector import RedditCollector
        from aggre.collectors.reddit.config import RedditConfig, RedditSource

        post = reddit_post(
            post_id="self1",
            selftext="My self-post body",
            is_self=True,
        )
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        with engine.connect() as conn:
            sc = conn.execute(sa.select(SilverContent)).fetchone()
            assert sc is not None and sc.text == "My self-post body"

    def test_lobsters_link_post_does_not_set_text(self, engine, mock_http, log):
        """Lobsters link post: SilverContent.text must remain NULL."""
        from aggre.collectors.lobsters.collector import LobstersCollector
        from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource

        story = lobsters_story(
            short_id="link1",
            url="https://example.com/article",
        )
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[story])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collect(LobstersCollector(), engine, config.lobsters, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.canonical_url.like("%example.com%"),
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "Lobsters collector must not set SilverContent.text for link posts"

    def test_rss_does_not_set_text(self, engine, log):
        """RSS collector: SilverContent.text must remain NULL (summary goes to observation.content_text)."""
        from aggre.collectors.rss.collector import RssCollector
        from aggre.collectors.rss.config import RssConfig, RssSource

        entry = rss_entry(
            id="post-1",
            title="First Post",
            link="https://example.com/post-1",
            summary="Article summary",
        )
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            config = make_config(rss=RssConfig(sources=[RssSource(name="Test Blog", url="https://example.com/feed.xml")]))
            collect(RssCollector(), engine, config.rss, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "RSS collector must not set SilverContent.text"

    def test_youtube_does_not_set_text(self, engine, log):
        """YouTube collector: SilverContent.text must remain NULL (transcription pipeline handles it)."""
        from aggre.collectors.youtube.collector import YoutubeCollector
        from aggre.collectors.youtube.config import YoutubeConfig, YoutubeSource

        entries = [youtube_entry(video_id="vid001", title="Test Video")]
        mock_ydl = MagicMock()
        mock_ydl.extract_info = lambda url, download=False: {"entries": entries}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            config = make_config(
                youtube=YoutubeConfig(sources=[YoutubeSource(channel_id="UC_test", name="Test Channel")]),
            )
            collect(YoutubeCollector(), engine, config.youtube, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "YouTube collector must not set SilverContent.text"

    def test_huggingface_does_not_set_text(self, engine, mock_http, log):
        """HuggingFace collector: SilverContent.text must remain NULL (summary goes to observation.content_text)."""
        from aggre.collectors.huggingface.collector import HuggingfaceCollector
        from aggre.collectors.huggingface.config import HuggingfaceConfig, HuggingfaceSource

        mock_http.get("https://huggingface.co/api/daily_papers").respond(json=[hf_paper()])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HF Papers")]))
        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "HuggingFace collector must not set SilverContent.text"

    def test_hackernews_external_content_stays_in_download_pipeline(self, engine, mock_http, log):
        """After HN collector runs, external content must appear in the download query."""
        from aggre.collectors.hackernews.collector import HackernewsCollector
        from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hn_hit()),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource()]))
            collect(HackernewsCollector(), engine, config.hackernews, config.settings, log)

        with engine.connect() as conn:
            pending = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.is_(None),
                )
            ).fetchall()
            assert len(pending) == 1, "External content must be pending download after collection"

    def test_rss_content_stays_in_download_pipeline(self, engine, log):
        """After RSS collector runs, linked content must appear in the download query."""
        from aggre.collectors.rss.collector import RssCollector
        from aggre.collectors.rss.config import RssConfig, RssSource

        entry = rss_entry(id="rss-1", link="https://example.com/rss-article")
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            config = make_config(rss=RssConfig(sources=[RssSource(name="Feed", url="https://example.com/feed.xml")]))
            collect(RssCollector(), engine, config.rss, config.settings, log)

        with engine.connect() as conn:
            pending = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverContent.fetched_at.is_(None),
                )
            ).fetchall()
            assert len(pending) == 1, "RSS linked content must be pending download after collection"

    def test_youtube_content_stays_in_transcription_pipeline(self, engine, log):
        """After YouTube collector runs, content must be eligible for transcription."""
        from aggre.collectors.youtube.collector import YoutubeCollector
        from aggre.collectors.youtube.config import YoutubeConfig, YoutubeSource

        entries = [youtube_entry(video_id="vid_pipe")]
        mock_ydl = MagicMock()
        mock_ydl.extract_info = lambda url, download=False: {"entries": entries}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            config = make_config(
                youtube=YoutubeConfig(sources=[YoutubeSource(channel_id="UC_test", name="Test Channel")]),
            )
            collect(YoutubeCollector(), engine, config.youtube, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent.id)
                .join(SilverObservation, SilverObservation.content_id == SilverContent.id)
                .where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                    SilverObservation.source_type == "youtube",
                )
            ).fetchall()
            assert len(rows) == 1, "YouTube content must be eligible for transcription after collection"
