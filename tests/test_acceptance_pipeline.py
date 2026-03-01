"""Acceptance tests: comments as JSON, full pipeline flow, content fetcher integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource
from aggre.collectors.reddit.collector import RedditCollector
from aggre.collectors.reddit.config import RedditConfig, RedditSource
from aggre.collectors.rss.collector import RssCollector
from aggre.collectors.rss.config import RssConfig, RssSource
from aggre.dagster_defs.webpage.job import download_content, extract_html_text
from aggre.db import SilverContent, SilverDiscussion
from aggre.tracking.model import StageTracking
from aggre.tracking.status import Stage, StageStatus
from tests.factories import (
    hn_comment_child,
    hn_hit,
    hn_item_response,
    hn_search_response,
    lobsters_comment,
    lobsters_story,
    lobsters_story_detail,
    make_config,
    reddit_comment,
    reddit_comment_listing,
    reddit_listing,
    reddit_post,
    rss_entry,
    rss_feed,
    seed_content,
)
from tests.helpers import assert_no_tracking, assert_tracking, collect, get_contents, get_discussions

pytestmark = pytest.mark.acceptance

# ===========================================================================
# Part 1: Comments stored as raw JSON
# ===========================================================================


class TestCommentsAsJsonReddit:
    """Reddit: collect -> collect_comments -> verify comments_json on SilverDiscussion."""

    def test_comments_stored_as_json(self, engine, mock_http):
        config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
        collector = RedditCollector()

        # Step 1: collect posts
        post = reddit_post()
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            collect(collector, engine, config.reddit, config.settings)

        # Step 2: collect_comments — reset mock_http for new routes
        mock_http.reset()
        c1 = reddit_comment(comment_id="rc1", body="First!")
        c2 = reddit_comment(comment_id="rc2", body="Second!", parent_id="t1_rc1")
        comment_resp = reddit_comment_listing(c1, c2)
        mock_http.get(url__regex=r".*/comments/abc123\.json.*").respond(json=comment_resp)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            fetched = collector.collect_comments(engine, config.reddit, config.settings, batch_limit=10)

        assert fetched == 1

        # Step 3: verify
        disc = get_discussions(engine)[0]
        assert disc.comments_json is not None
        comments = json.loads(disc.comments_json)
        assert len(comments) == 2
        assert comments[0]["data"]["body"] == "First!"
        assert comments[1]["data"]["body"] == "Second!"
        assert disc.comment_count == 2

    def test_no_bronze_or_silver_comments_tables(self, engine):
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()
        assert "bronze_comments" not in tables
        assert "silver_comments" not in tables


class TestCommentsAsJsonHackernews:
    """HackerNews: collect -> collect_comments -> verify comments_json."""

    def test_comments_stored_as_json(self, engine, mock_http):
        config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]))
        collector = HackernewsCollector()

        # Step 1: collect
        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(json=hn_search_response(hit))

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        # Step 2: collect_comments — reset mock_http for new routes
        mock_http.reset()
        c1 = hn_comment_child(comment_id=100, text="HN first!")
        c2 = hn_comment_child(comment_id=101, text="HN second!")
        item_resp = hn_item_response(object_id="12345", children=[c1, c2])
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/items/12345").respond(json=item_resp)

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            fetched = collector.collect_comments(engine, config.hackernews, config.settings, batch_limit=10)

        assert fetched == 1

        # Step 3: verify
        disc = get_discussions(engine)[0]
        assert disc.comments_json is not None
        comments = json.loads(disc.comments_json)
        assert len(comments) == 2
        assert comments[0]["text"] == "HN first!"
        assert comments[1]["text"] == "HN second!"
        assert disc.comment_count == 2

    def test_no_bronze_or_silver_comments_tables(self, engine):
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()
        assert "bronze_comments" not in tables
        assert "silver_comments" not in tables


class TestCommentsAsJsonLobsters:
    """Lobsters: collect -> collect_comments -> verify comments_json."""

    def test_comments_stored_as_json(self, engine, mock_http):
        config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
        collector = LobstersCollector()

        # Step 1: collect
        story = lobsters_story()
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            collect(collector, engine, config.lobsters, config.settings)

        # Step 2: collect_comments — reset mock_http for new routes
        mock_http.reset()
        c1 = lobsters_comment(short_id="lc1", comment="Lobsters first!")
        c2 = lobsters_comment(short_id="lc2", comment="Lobsters second!")
        detail = lobsters_story_detail(short_id="abc123", comments=[c1, c2])
        mock_http.get(url__regex=r"s/abc123\.json").respond(json=detail)

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            fetched = collector.collect_comments(engine, config.lobsters, config.settings, batch_limit=10)

        assert fetched == 1

        # Step 3: verify
        disc = get_discussions(engine)[0]
        assert disc.comments_json is not None
        comments = json.loads(disc.comments_json)
        assert len(comments) == 2
        assert comments[0]["comment"] == "Lobsters first!"
        assert comments[1]["comment"] == "Lobsters second!"
        assert disc.comment_count == 2

    def test_no_bronze_or_silver_comments_tables(self, engine):
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()
        assert "bronze_comments" not in tables
        assert "silver_comments" not in tables


# ===========================================================================
# Part 2: Full pipeline flow
# ===========================================================================


class TestFullPipelineFlow:
    """Simulate fetch pipeline: collect -> collect_comments -> fetch_content."""

    def test_rss_pipeline_creates_full_chain(self, engine, mock_http):
        config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://blog.example.com/feed.xml")]))

        # Step 1: Collect RSS posts
        entry = rss_entry(
            id="rss-1",
            title="Great Article",
            link="https://blog.example.com/great-article",
            summary="A teaser summary",
        )
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            rss = RssCollector()
            count = collect(rss, engine, config.rss, config.settings)

        assert count == 1

        # Verify SilverDiscussion exists with content_id
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc is not None
            assert disc.title == "Great Article"
            assert disc.content_id is not None

            # Verify SilverContent exists in unprocessed state
            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == disc.content_id)).fetchone()
            assert content is not None
            assert content.text is None
            assert "blog.example.com" in content.canonical_url

        # Step 2: RSS has no comments, skip

        # Step 3: Download pending content
        mock_http.get("https://blog.example.com/great-article").respond(
            text="<html><body><p>Full article body here</p></body></html>",
            headers={"content-type": "text/html"},
        )

        downloaded = download_content(engine, config)

        assert downloaded == 1

        # Verify intermediate state: downloaded but not yet extracted
        content = get_contents(engine)[0]
        assert content.text is None

        assert_tracking(engine, "webpage", content.canonical_url, Stage.DOWNLOAD, StageStatus.DONE)

        # Step 4: Extract text from downloaded HTML
        with (
            patch("aggre.dagster_defs.webpage.job.trafilatura.extract", return_value="Full article body here"),
            patch("aggre.dagster_defs.webpage.job.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            mock_meta_obj = MagicMock()
            mock_meta_obj.title = "Great Article - Full"
            mock_meta.return_value = mock_meta_obj

            extracted = extract_html_text(engine, config)

        assert extracted == 1

        # Verify full chain: SilverDiscussion -> SilverContent
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.content_id is not None

            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == disc.content_id)).fetchone()
            assert content.text == "Full article body here"
            assert content.title == "Great Article - Full"

    def test_reddit_pipeline_with_comments(self, engine, mock_http):
        """Reddit collect -> collect_comments -> verify discussion with comments."""
        config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
        collector = RedditCollector()

        # Step 1: collect
        post = reddit_post()
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            count = collect(collector, engine, config.reddit, config.settings)

        assert count == 1

        # Step 2: collect_comments — reset mock_http for new routes
        mock_http.reset()
        c1 = reddit_comment(comment_id="c1", body="Top comment")
        comment_resp = reddit_comment_listing(c1)
        mock_http.get(url__regex=r".*/comments/abc123\.json.*").respond(json=comment_resp)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            fetched = collector.collect_comments(engine, config.reddit, config.settings, batch_limit=10)

        assert fetched == 1

        # Verify full state
        disc = get_discussions(engine)[0]
        assert disc.title == "Test Post"
        assert disc.source_type == "reddit"
        assert disc.comments_json is not None
        assert disc.comment_count == 1


# ===========================================================================
# Part 3: Content fetcher integration
# ===========================================================================


class TestContentFetcherIntegration:
    """Content fetcher: unprocessed -> downloaded -> text populated / error set."""

    def test_download_then_extract_populates_fields(self, engine, mock_http):
        config = make_config()

        seed_content(engine, "https://example.com/article-1", domain="example.com")
        seed_content(engine, "https://example.com/article-2", domain="example.com")

        mock_http.get("https://example.com/article-1").respond(
            text="<html><body>Content</body></html>",
            headers={"content-type": "text/html"},
        )
        mock_http.get("https://example.com/article-2").respond(
            text="<html><body>Content</body></html>",
            headers={"content-type": "text/html"},
        )

        count = download_content(engine, config)

        assert count == 2

        # Verify intermediate state
        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).order_by(SilverContent.id)).fetchall()
            for row in rows:
                assert row.text is None

            tracking_rows = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "webpage",
                    StageTracking.stage == Stage.DOWNLOAD,
                    StageTracking.status == StageStatus.DONE,
                )
            ).fetchall()
            assert len(tracking_rows) == 2

        with (
            patch("aggre.dagster_defs.webpage.job.trafilatura.extract", return_value="Extracted text"),
            patch("aggre.dagster_defs.webpage.job.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            meta_obj = MagicMock()
            meta_obj.title = "Article Title"
            mock_meta.return_value = meta_obj

            count = extract_html_text(engine, config)

        assert count == 2

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).order_by(SilverContent.id)).fetchall()
            for row in rows:
                assert row.text == "Extracted text"
                assert row.title == "Article Title"

    def test_youtube_urls_skipped(self, engine):
        config = make_config()

        seed_content(engine, "https://youtube.com/watch?v=abc", domain="youtube.com")
        seed_content(engine, "https://youtu.be/xyz", domain="youtu.be")

        count = download_content(engine, config)
        assert count == 0  # YouTube URLs excluded from download query (handled by transcription)

    def test_failed_download_stores_error(self, engine, mock_http):
        config = make_config()

        seed_content(engine, "https://broken.example.com/page", domain="broken.example.com")

        mock_http.get("https://broken.example.com/page").mock(side_effect=Exception("Connection timeout"))

        count = download_content(engine, config)

        assert count == 1

        assert_tracking(
            engine,
            "webpage",
            "https://broken.example.com/page",
            Stage.DOWNLOAD,
            StageStatus.FAILED,
            error_contains="Connection timeout",
        )

    def test_mixed_statuses(self, engine, mock_http):
        """One normal, one YouTube (excluded), one failing -- download step only."""
        config = make_config()

        seed_content(engine, "https://example.com/good", domain="example.com")
        seed_content(engine, "https://youtube.com/watch?v=vid1", domain="youtube.com")
        seed_content(engine, "https://bad.example.com/broken", domain="bad.example.com")

        mock_http.get("https://example.com/good").respond(
            text="<html><body>Good content</body></html>",
            headers={"content-type": "text/html"},
        )
        mock_http.get("https://bad.example.com/broken").mock(side_effect=Exception("DNS failure"))

        count = download_content(engine, config)

        # YouTube excluded from download query (handled by transcription), so only 2 processed
        assert count == 2

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).order_by(SilverContent.id)).fetchall()

            # good article — downloaded (text still NULL)
            assert rows[0].text is None

        # Check tracking for each
        assert_tracking(engine, "webpage", "https://example.com/good", Stage.DOWNLOAD, StageStatus.DONE)

        # YouTube URL has no download tracking (excluded from download pipeline)
        assert_no_tracking(engine, "webpage", "https://youtube.com/watch?v=vid1", Stage.DOWNLOAD)

        assert_tracking(
            engine,
            "webpage",
            "https://bad.example.com/broken",
            Stage.DOWNLOAD,
            StageStatus.FAILED,
            error_contains="DNS failure",
        )

        # Now extract the downloaded one
        with (
            patch("aggre.dagster_defs.webpage.job.trafilatura.extract", return_value="Good body"),
            patch("aggre.dagster_defs.webpage.job.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            meta_obj = MagicMock()
            meta_obj.title = "Good Title"
            mock_meta.return_value = meta_obj

            count = extract_html_text(engine, config)

        assert count == 1

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).order_by(SilverContent.id)).fetchall()

            assert rows[0].text == "Good body"
            assert rows[0].title == "Good Title"

    def test_already_processed_not_reprocessed(self, engine):
        config = make_config()

        seed_content(engine, "https://example.com/done", domain="example.com", text="already done")

        count = download_content(engine, config)
        assert count == 0
