"""Invariant tests: guard the stage tracking state machine and collector content constraints.

These tests protect the core architectural invariant: Silver columns hold entity data,
stage_tracking controls processing state. If a collector accidentally writes to
SilverContent.text, rows fall out of the download/extract/transcription pipeline.

Stage tracking state machine (per stage):
    No tracking row               -> needs processing (sensor discovers from Silver state)
    status='done'                 -> completed
    status='failed', retries < max -> retry eligible
    status='failed', retries >= max -> exhausted (skipped)
    status='skipped'              -> permanently skipped

Silver-level discovery:
    text=NULL                     -> needs download/transcription
    text=SET                      -> needs discussion search (if no discussion_search tracking)

For SilverDiscussion:
    comments_json=NULL            -> needs comments (if no comments tracking)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
import sqlalchemy.orm

from aggre.db import SilverContent, SilverDiscussion
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter, upsert_done, upsert_failed, upsert_skipped
from aggre.tracking.status import Stage, StageStatus
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
    seed_discussion,
    youtube_entry,
)
from tests.helpers import collect

pytestmark = pytest.mark.integration


def _backdate_last_ran(engine: sa.engine.Engine, external_id: str, stage: Stage) -> None:
    """Set last_ran_at far enough in the past to pass the cooldown check."""
    with engine.begin() as conn:
        conn.execute(
            sa.update(StageTracking)
            .where(
                StageTracking.external_id == external_id,
                StageTracking.stage == stage,
            )
            .values(last_ran_at="2000-01-01T00:00:00+00:00")
        )


# ---------------------------------------------------------------------------
# Stage tracking state machine queries
# ---------------------------------------------------------------------------


def _download_query():
    """Build the download sensor query (content needing download)."""
    from aggre.workflows.webpage import SKIP_DOMAINS

    return (
        sa.select(SilverContent.id, SilverContent.canonical_url)
        .outerjoin(
            StageTracking,
            sa.and_(
                StageTracking.source == "webpage",
                StageTracking.external_id == SilverContent.canonical_url,
                StageTracking.stage == Stage.DOWNLOAD,
            ),
        )
        .where(
            SilverContent.text.is_(None),
            sa.or_(
                SilverContent.domain.notin_(SKIP_DOMAINS),
                SilverContent.domain.is_(None),
            ),
            sa.or_(
                StageTracking.id.is_(None),
                retry_filter(StageTracking, Stage.DOWNLOAD),
            ),
            sa.not_(sa.func.coalesce(StageTracking.status == StageStatus.SKIPPED, False)),
        )
    )


def _extract_query():
    """Build the extract query (content with download done, needing extraction)."""
    download_done_sq = (
        sa.select(StageTracking.external_id)
        .where(
            StageTracking.source == "webpage",
            StageTracking.stage == Stage.DOWNLOAD,
            StageTracking.status == StageStatus.DONE,
        )
        .subquery()
    )
    st_extract = sa.orm.aliased(StageTracking)
    return (
        sa.select(SilverContent.id, SilverContent.canonical_url)
        .where(
            SilverContent.text.is_(None),
            SilverContent.canonical_url.in_(sa.select(download_done_sq.c.external_id)),
        )
        .outerjoin(
            st_extract,
            sa.and_(
                st_extract.source == "webpage",
                st_extract.external_id == SilverContent.canonical_url,
                st_extract.stage == Stage.EXTRACT,
            ),
        )
        .where(
            sa.or_(
                st_extract.id.is_(None),
                retry_filter(st_extract, Stage.EXTRACT),
            ),
        )
    )


def _transcription_query():
    """Build the transcription sensor query."""
    return (
        sa.select(SilverContent.id)
        .join(SilverDiscussion, SilverDiscussion.content_id == SilverContent.id)
        .outerjoin(
            StageTracking,
            sa.and_(
                StageTracking.source == "youtube",
                StageTracking.external_id == SilverDiscussion.external_id,
                StageTracking.stage == Stage.TRANSCRIBE,
            ),
        )
        .where(
            SilverContent.text.is_(None),
            SilverDiscussion.source_type == "youtube",
            sa.or_(
                StageTracking.id.is_(None),
                retry_filter(StageTracking, Stage.TRANSCRIBE),
            ),
        )
    )


def _discussion_search_query():
    """Build the discussion search sensor query."""
    return (
        sa.select(SilverContent.id)
        .outerjoin(
            StageTracking,
            sa.and_(
                StageTracking.source == "webpage",
                StageTracking.external_id == SilverContent.canonical_url,
                StageTracking.stage == Stage.DISCUSSION_SEARCH,
            ),
        )
        .where(
            SilverContent.text.isnot(None),
            SilverContent.canonical_url.isnot(None),
            sa.or_(
                StageTracking.id.is_(None),
                retry_filter(StageTracking, Stage.DISCUSSION_SEARCH),
            ),
        )
    )


def _comments_query():
    """Build the comments sensor query."""
    return (
        sa.select(SilverDiscussion.id)
        .outerjoin(
            StageTracking,
            sa.and_(
                StageTracking.source == SilverDiscussion.source_type,
                StageTracking.external_id == SilverDiscussion.external_id,
                StageTracking.stage == Stage.COMMENTS,
            ),
        )
        .where(
            SilverDiscussion.source_type.in_(["reddit", "hackernews", "lobsters"]),
            SilverDiscussion.comments_json.is_(None),
            sa.or_(
                StageTracking.id.is_(None),
                retry_filter(StageTracking, Stage.COMMENTS),
            ),
        )
    )


class TestStageTrackingStateTransitions:
    """Verify the stage tracking state machine queries find/exclude rows correctly."""

    def test_pending_content_found_by_download_query(self, engine):
        """text=NULL, no tracking -> found by download query."""
        seed_content(engine, "https://example.com/pending", domain="example.com")

        with engine.connect() as conn:
            rows = conn.execute(_download_query()).fetchall()
            assert len(rows) == 1

    def test_pending_content_excluded_from_extract_query(self, engine):
        """text=NULL, no download tracking -> NOT found by extract query."""
        seed_content(engine, "https://example.com/pending", domain="example.com")

        with engine.connect() as conn:
            rows = conn.execute(_extract_query()).fetchall()
            assert len(rows) == 0

    def test_download_done_found_by_extract_query(self, engine):
        """text=NULL, download tracking done -> found by extract query."""
        seed_content(engine, "https://example.com/downloaded", domain="example.com")
        upsert_done(engine, "webpage", "https://example.com/downloaded", Stage.DOWNLOAD)

        with engine.connect() as conn:
            rows = conn.execute(_extract_query()).fetchall()
            assert len(rows) == 1

    def test_download_done_excluded_from_download_query(self, engine):
        """text=NULL, download tracking done -> NOT found by download query."""
        seed_content(engine, "https://example.com/downloaded", domain="example.com")
        upsert_done(engine, "webpage", "https://example.com/downloaded", Stage.DOWNLOAD)

        with engine.connect() as conn:
            rows = conn.execute(_download_query()).fetchall()
            assert len(rows) == 0

    def test_download_skipped_excluded_from_download_query(self, engine):
        """Download tracking skipped -> NOT found by download query."""
        seed_content(engine, "https://example.com/skipped", domain="example.com")
        upsert_skipped(engine, "webpage", "https://example.com/skipped", Stage.DOWNLOAD, "pdf")

        with engine.connect() as conn:
            rows = conn.execute(_download_query()).fetchall()
            assert len(rows) == 0

    def test_download_failed_retries_below_max_found_by_download_query(self, engine):
        """Download failed, retries < max, past cooldown -> found by download query (retry)."""
        seed_content(engine, "https://example.com/retry", domain="example.com")
        upsert_failed(engine, "webpage", "https://example.com/retry", Stage.DOWNLOAD, "timeout")
        _backdate_last_ran(engine, "https://example.com/retry", Stage.DOWNLOAD)

        with engine.connect() as conn:
            rows = conn.execute(_download_query()).fetchall()
            assert len(rows) == 1

    def test_download_failed_retries_at_max_excluded_from_download_query(self, engine):
        """Download failed, retries >= max -> NOT found by download query."""
        seed_content(engine, "https://example.com/exhausted", domain="example.com")
        # Exhaust retries (max is 3)
        for _ in range(3):
            upsert_failed(engine, "webpage", "https://example.com/exhausted", Stage.DOWNLOAD, "timeout")

        with engine.connect() as conn:
            rows = conn.execute(_download_query()).fetchall()
            assert len(rows) == 0

    def test_completed_content_excluded_from_download_and_transcription(self, engine):
        """text=SET -> NOT found by download or transcription queries."""
        cid = seed_content(engine, "https://example.com/done", domain="example.com", text="done")
        seed_discussion(engine, source_type="youtube", external_id="done", content_id=cid)

        with engine.connect() as conn:
            download = conn.execute(_download_query()).fetchall()
            transcription = conn.execute(_transcription_query()).fetchall()
            assert len(download) == 0
            assert len(transcription) == 0

    def test_text_set_no_search_tracking_found_by_discussion_search_query(self, engine):
        """text=SET, no discussion_search tracking -> found by discussion search query."""
        seed_content(engine, "https://example.com/to-search", domain="example.com", text="hello")

        with engine.connect() as conn:
            rows = conn.execute(_discussion_search_query()).fetchall()
            assert len(rows) == 1

    def test_discussion_search_done_excluded_from_discussion_search_query(self, engine):
        """Discussion search tracking done -> NOT found by discussion search query."""
        seed_content(engine, "https://example.com/searched", domain="example.com", text="hello")
        upsert_done(engine, "webpage", "https://example.com/searched", Stage.DISCUSSION_SEARCH)

        with engine.connect() as conn:
            rows = conn.execute(_discussion_search_query()).fetchall()
            assert len(rows) == 0

    def test_youtube_content_found_by_transcription_query(self, engine):
        """YouTube content (text=NULL, no tracking) -> found by transcription query."""
        cid = seed_content(engine, "https://youtube.com/watch?v=abc", domain="youtube.com")
        seed_discussion(engine, source_type="youtube", external_id="abc", content_id=cid)

        with engine.connect() as conn:
            rows = conn.execute(_transcription_query()).fetchall()
            assert len(rows) == 1

    def test_non_youtube_excluded_from_transcription_query(self, engine):
        """Non-YouTube content -> NOT found by transcription query."""
        cid = seed_content(engine, "https://example.com/article", domain="example.com")
        seed_discussion(engine, source_type="hackernews", external_id="12345", content_id=cid)

        with engine.connect() as conn:
            rows = conn.execute(_transcription_query()).fetchall()
            assert len(rows) == 0

    def test_completed_youtube_excluded_from_transcription_query(self, engine):
        """YouTube content with text=SET -> NOT found by transcription query."""
        cid = seed_content(
            engine,
            "https://youtube.com/watch?v=done",
            domain="youtube.com",
            text="transcript here",
        )
        seed_discussion(engine, source_type="youtube", external_id="done", content_id=cid)

        with engine.connect() as conn:
            rows = conn.execute(_transcription_query()).fetchall()
            assert len(rows) == 0

    def test_pending_comments_found_by_comments_query(self, engine):
        """comments_json=NULL, no tracking, source_type in list -> found."""
        seed_discussion(engine, source_type="reddit", external_id="abc123")

        with engine.connect() as conn:
            rows = conn.execute(_comments_query()).fetchall()
            assert len(rows) == 1

    def test_completed_comments_excluded_from_comments_query(self, engine):
        """comments_json=SET -> excluded from comments query."""
        seed_discussion(
            engine,
            source_type="reddit",
            external_id="done456",
            comments_json='[{"body": "hi"}]',
        )

        with engine.connect() as conn:
            rows = conn.execute(_comments_query()).fetchall()
            assert len(rows) == 0

    def test_comments_tracking_done_excluded_from_comments_query(self, engine):
        """comments_json=NULL but tracking done -> excluded from comments query."""
        seed_discussion(engine, source_type="hackernews", external_id="tracked01")
        upsert_done(engine, "hackernews", "tracked01", Stage.COMMENTS)

        with engine.connect() as conn:
            rows = conn.execute(_comments_query()).fetchall()
            assert len(rows) == 0

    def test_mixed_states_correctly_routed(self, engine):
        """Multiple rows in different states are each picked up by the right query."""
        # Pending download (text=NULL, no tracking)
        seed_content(engine, "https://example.com/pending", domain="example.com")
        # Downloaded, awaiting extraction (download tracking done, text=NULL)
        seed_content(engine, "https://example.com/downloaded", domain="example.com")
        upsert_done(engine, "webpage", "https://example.com/downloaded", Stage.DOWNLOAD)
        # Completed (text=SET)
        seed_content(engine, "https://example.com/done", domain="example.com", text="done")
        # Download failed (tracking failed, retries=1, past cooldown)
        seed_content(engine, "https://example.com/failed", domain="example.com")
        upsert_failed(engine, "webpage", "https://example.com/failed", Stage.DOWNLOAD, "timeout")
        _backdate_last_ran(engine, "https://example.com/failed", Stage.DOWNLOAD)

        with engine.connect() as conn:
            download = conn.execute(_download_query()).fetchall()
            extract = conn.execute(_extract_query()).fetchall()

            download_urls = {r.canonical_url for r in download}
            extract_urls = {r.canonical_url for r in extract}

            # Pending + failed (retry eligible) found by download query
            assert "https://example.com/pending" in download_urls
            assert "https://example.com/failed" in download_urls
            assert len(download) == 2

            # Only downloaded found by extract query
            assert extract_urls == {"https://example.com/downloaded"}
            assert len(extract) == 1


# ---------------------------------------------------------------------------
# Collector content constraints: collectors must NOT set SilverContent.text
# (except for self-posts via _ensure_self_post_content)
# ---------------------------------------------------------------------------


class TestCollectorContentConstraints:
    """Verify collectors do NOT write to SilverContent.text (would break null-check pipeline).

    Self-posts are the exception: they write text via _ensure_self_post_content because
    there is no external URL to download.
    """

    def test_hackernews_external_does_not_set_text(self, engine, mock_http):
        """HN collector with external URL: SilverContent.text must remain NULL."""
        from aggre.collectors.hackernews.collector import HackernewsCollector
        from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hn_hit()),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource()]))
            collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "HN collector must not set SilverContent.text for external URLs"

    def test_hackernews_self_post_sets_text(self, engine, mock_http):
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
            collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        with engine.connect() as conn:
            sc = conn.execute(sa.select(SilverContent)).fetchone()
            assert sc is not None and sc.text == "Self-post content"

    def test_reddit_link_post_does_not_set_text(self, engine, mock_http):
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
            collect(RedditCollector(), engine, config.reddit, config.settings)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.canonical_url.like("%example.com%"),
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "Reddit collector must not set SilverContent.text for link posts"

    def test_reddit_self_post_sets_text(self, engine, mock_http):
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
            collect(RedditCollector(), engine, config.reddit, config.settings)

        with engine.connect() as conn:
            sc = conn.execute(sa.select(SilverContent)).fetchone()
            assert sc is not None and sc.text == "My self-post body"

    def test_lobsters_link_post_does_not_set_text(self, engine, mock_http):
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
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.canonical_url.like("%example.com%"),
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "Lobsters collector must not set SilverContent.text for link posts"

    def test_rss_does_not_set_text(self, engine):
        """RSS collector: SilverContent.text must remain NULL (summary goes to discussion.content_text)."""
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
            collect(RssCollector(), engine, config.rss, config.settings)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "RSS collector must not set SilverContent.text"

    def test_youtube_does_not_set_text(self, engine):
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
            collect(YoutubeCollector(), engine, config.youtube, config.settings)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "YouTube collector must not set SilverContent.text"

    def test_huggingface_does_not_set_text(self, engine, mock_http):
        """HuggingFace collector: SilverContent.text must remain NULL (summary goes to discussion.content_text)."""
        from aggre.collectors.huggingface.collector import HuggingfaceCollector
        from aggre.collectors.huggingface.config import HuggingfaceConfig, HuggingfaceSource

        mock_http.get("https://huggingface.co/api/daily_papers").respond(json=[hf_paper()])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HF Papers")]))
        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(rows) == 1
            assert rows[0].text is None, "HuggingFace collector must not set SilverContent.text"

    def test_hackernews_external_content_stays_in_download_pipeline(self, engine, mock_http):
        """After HN collector runs, external content must appear in the download query."""
        from aggre.collectors.hackernews.collector import HackernewsCollector
        from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hn_hit()),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource()]))
            collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        with engine.connect() as conn:
            pending = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                )
            ).fetchall()
            assert len(pending) == 1, "External content must be pending download after collection"

    def test_rss_content_stays_in_download_pipeline(self, engine):
        """After RSS collector runs, linked content must appear in the download query."""
        from aggre.collectors.rss.collector import RssCollector
        from aggre.collectors.rss.config import RssConfig, RssSource

        entry = rss_entry(id="rss-1", link="https://example.com/rss-article")
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            config = make_config(rss=RssConfig(sources=[RssSource(name="Feed", url="https://example.com/feed.xml")]))
            collect(RssCollector(), engine, config.rss, config.settings)

        with engine.connect() as conn:
            pending = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                )
            ).fetchall()
            assert len(pending) == 1, "RSS linked content must be pending download after collection"

    def test_youtube_content_stays_in_transcription_pipeline(self, engine):
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
            collect(YoutubeCollector(), engine, config.youtube, config.settings)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent.id)
                .join(SilverDiscussion, SilverDiscussion.content_id == SilverContent.id)
                .where(
                    SilverContent.text.is_(None),
                    SilverDiscussion.source_type == "youtube",
                )
            ).fetchall()
            assert len(rows) == 1, "YouTube content must be eligible for transcription after collection"
