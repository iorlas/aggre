"""Acceptance tests: comments as JSON, full pipeline flow, content fetcher integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.collectors.hackernews import HackernewsCollector
from aggre.collectors.lobsters import LobstersCollector
from aggre.collectors.reddit import RedditCollector
from aggre.collectors.rss import RssCollector
from aggre.collectors.hackernews.config import HackernewsConfig
from aggre.collectors.lobsters.config import LobstersConfig
from aggre.collectors.reddit.config import RedditConfig
from aggre.collectors.rss.config import RssConfig
from aggre.config import (
    AppConfig,
    HackernewsSource,
    LobstersSource,
    RedditSource,
    RssSource,
    Settings,
)
from aggre.content_fetcher import download_content, extract_html_text
from aggre.db import BronzeDiscussion, SilverContent, SilverDiscussion


# ---------------------------------------------------------------------------
# Reddit helpers
# ---------------------------------------------------------------------------

def _reddit_post(post_id="abc123", title="Reddit Post", subreddit="python"):
    return {
        "kind": "t3",
        "data": {
            "name": f"t3_{post_id}",
            "title": title,
            "author": "redditor",
            "selftext": "Self text body",
            "permalink": f"/r/{subreddit}/comments/{post_id}/slug/",
            "created_utc": 1700000000.0,
            "score": 50,
            "num_comments": 3,
            "link_flair_text": None,
            "subreddit": subreddit,
        },
    }


def _reddit_listing(*posts):
    return {"data": {"children": list(posts)}}


def _reddit_comment(comment_id="rc1", body="Reddit comment!", author="commenter", parent_id="t3_abc123"):
    return {
        "kind": "t1",
        "data": {
            "name": f"t1_{comment_id}",
            "author": author,
            "body": body,
            "score": 7,
            "parent_id": parent_id,
            "created_utc": 1700001000.0,
            "replies": "",
        },
    }


def _reddit_comment_listing(*comments):
    post_part = {"data": {"children": [_reddit_post()]}}
    comment_part = {"data": {"children": list(comments)}}
    return [post_part, comment_part]


def _reddit_fake_get(responses):
    def fake_get(url):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        for key, data in responses.items():
            if key in url:
                resp.json.return_value = data
                return resp
        resp.json.return_value = _reddit_listing()
        return resp
    return fake_get


# ---------------------------------------------------------------------------
# HackerNews helpers
# ---------------------------------------------------------------------------

def _hn_hit(object_id="12345", title="HN Story", url="https://example.com/hn-article"):
    return {
        "objectID": object_id,
        "title": title,
        "author": "hnuser",
        "url": url,
        "points": 80,
        "num_comments": 12,
        "created_at": "2024-01-15T12:00:00.000Z",
    }


def _hn_search_response(*hits):
    return {"hits": list(hits)}


def _hn_item_response(object_id="12345", children=None):
    return {"id": int(object_id), "children": children or []}


def _hn_comment_child(comment_id=100, text="HN comment!", author="hncommenter", children=None):
    return {
        "id": comment_id,
        "author": author,
        "text": text,
        "points": 3,
        "parent_id": 12345,
        "created_at": "2024-01-15T13:00:00.000Z",
        "children": children or [],
    }


def _hn_mock_client(responses):
    client = MagicMock()

    def fake_get(url):
        resp = MagicMock()
        resp.status_code = 200
        for pattern, data in responses.items():
            if pattern in url:
                resp.json.return_value = data
                return resp
        resp.json.return_value = {"hits": []}
        return resp

    client.get.side_effect = fake_get
    return client


# ---------------------------------------------------------------------------
# Lobsters helpers
# ---------------------------------------------------------------------------

def _lobsters_story(short_id="lob123", title="Lobsters Story", url="https://example.com/lob-article"):
    return {
        "short_id": short_id,
        "title": title,
        "url": url,
        "score": 15,
        "comment_count": 4,
        "tags": ["programming"],
        "submitter_user": "lobuser",
        "created_at": "2024-01-15T12:00:00.000Z",
        "comments_url": f"https://lobste.rs/s/{short_id}",
    }


def _lobsters_story_detail(short_id="lob123", comments=None):
    story = _lobsters_story(short_id=short_id)
    story["comments"] = comments or []
    return story


def _lobsters_comment(short_id="lc1", comment="Lobsters comment!", username="lobcommenter"):
    return {
        "short_id": short_id,
        "comment": comment,
        "commenting_user": {"username": username},
        "score": 4,
        "indent_level": 1,
        "parent_comment": None,
        "created_at": "2024-01-15T13:00:00.000Z",
    }


def _lobsters_mock_client(responses):
    client = MagicMock()

    def fake_get(url):
        resp = MagicMock()
        resp.status_code = 200
        for pattern, data in responses.items():
            if pattern in url:
                resp.json.return_value = data
                return resp
        resp.json.return_value = []
        return resp

    client.get.side_effect = fake_get
    return client


# ---------------------------------------------------------------------------
# RSS helpers
# ---------------------------------------------------------------------------

def _rss_entry(**kwargs):
    defaults = {
        "id": "rss-entry-1",
        "title": "RSS Article",
        "link": "https://example.com/rss-article",
        "author": "blogger",
        "summary": "RSS summary text",
        "published": "2025-01-01T00:00:00Z",
    }
    defaults.update(kwargs)
    data = {k: v for k, v in defaults.items() if v is not None}

    class Entry(dict):
        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError:
                return None

    return Entry(data)


def _rss_feed(entries, feed_title="Test Feed"):
    feed_meta = {"title": feed_title}

    class FeedMeta:
        def get(self, key, default=None):
            return feed_meta.get(key, default)

    class Feed:
        def __init__(self, entries, feed):
            self.entries = entries
            self.feed = feed
            self.bozo = False

    return Feed(entries, FeedMeta())


# ===========================================================================
# Part 1: Comments stored as raw JSON
# ===========================================================================


class TestCommentsAsJsonReddit:
    """Reddit: collect -> collect_comments -> verify comments_json on SilverDiscussion."""

    def test_comments_stored_as_json(self, engine):
        config = AppConfig(
            reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
            settings=Settings(reddit_rate_limit=0.0),
        )
        log = MagicMock()
        collector = RedditCollector()

        # Step 1: collect posts
        post = _reddit_post()
        listing = _reddit_listing(post)
        post_responses = {"hot.json": listing, "new.json": listing}

        with patch("aggre.collectors.reddit.collector.httpx.Client") as mock_cls, \
             patch("aggre.collectors.reddit.collector.time.sleep"):
            mock_cls.return_value = MagicMock(get=MagicMock(side_effect=_reddit_fake_get(post_responses)))
            collector.collect(engine, config.reddit, config.settings, log)

        # Step 2: collect_comments
        c1 = _reddit_comment(comment_id="rc1", body="First!")
        c2 = _reddit_comment(comment_id="rc2", body="Second!", parent_id="t1_rc1")
        comment_resp = _reddit_comment_listing(c1, c2)
        comment_responses = {"comments/abc123.json": comment_resp}

        with patch("aggre.collectors.reddit.collector.httpx.Client") as mock_cls, \
             patch("aggre.collectors.reddit.collector.time.sleep"):
            mock_cls.return_value = MagicMock(get=MagicMock(side_effect=_reddit_fake_get(comment_responses)))
            fetched = collector.collect_comments(engine, config.reddit, config.settings, log, batch_limit=10)

        assert fetched == 1

        # Step 3: verify
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.comments_json is not None
            comments = json.loads(disc.comments_json)
            assert len(comments) == 2
            assert comments[0]["data"]["body"] == "First!"
            assert comments[1]["data"]["body"] == "Second!"
            assert disc.comment_count == 2

            assert disc.comments_status == "done"

    def test_no_bronze_or_silver_comments_tables(self, engine):
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()
        assert "bronze_comments" not in tables
        assert "silver_comments" not in tables


class TestCommentsAsJsonHackernews:
    """HackerNews: collect -> collect_comments -> verify comments_json."""

    def test_comments_stored_as_json(self, engine):
        config = AppConfig(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            settings=Settings(hn_rate_limit=0.0),
        )
        log = MagicMock()
        collector = HackernewsCollector()

        # Step 1: collect
        hit = _hn_hit()
        responses = {"search_by_date": _hn_search_response(hit)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _hn_mock_client(responses)
            collector.collect(engine, config.hackernews, config.settings, log)

        # Step 2: collect_comments
        c1 = _hn_comment_child(comment_id=100, text="HN first!")
        c2 = _hn_comment_child(comment_id=101, text="HN second!")
        item_resp = _hn_item_response(object_id="12345", children=[c1, c2])
        comment_responses = {"items/12345": item_resp}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _hn_mock_client(comment_responses)
            fetched = collector.collect_comments(engine, config.hackernews, config.settings, log, batch_limit=10)

        assert fetched == 1

        # Step 3: verify
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.comments_json is not None
            comments = json.loads(disc.comments_json)
            assert len(comments) == 2
            assert comments[0]["text"] == "HN first!"
            assert comments[1]["text"] == "HN second!"
            assert disc.comment_count == 2

            # HN collector updates comments_status column directly
            assert disc.comments_status == "done"

    def test_no_bronze_or_silver_comments_tables(self, engine):
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()
        assert "bronze_comments" not in tables
        assert "silver_comments" not in tables


class TestCommentsAsJsonLobsters:
    """Lobsters: collect -> collect_comments -> verify comments_json."""

    def test_comments_stored_as_json(self, engine):
        config = AppConfig(
            lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]),
            settings=Settings(lobsters_rate_limit=0.0),
        )
        log = MagicMock()
        collector = LobstersCollector()

        # Step 1: collect
        story = _lobsters_story()
        responses = {"hottest.json": [story], "newest.json": []}

        with patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.lobsters.collector.time.sleep"):
            mock_cls.return_value = _lobsters_mock_client(responses)
            collector.collect(engine, config.lobsters, config.settings, log)

        # Step 2: collect_comments
        c1 = _lobsters_comment(short_id="lc1", comment="Lobsters first!")
        c2 = _lobsters_comment(short_id="lc2", comment="Lobsters second!")
        detail = _lobsters_story_detail(short_id="lob123", comments=[c1, c2])
        comment_responses = {"s/lob123.json": detail}

        with patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.lobsters.collector.time.sleep"):
            mock_cls.return_value = _lobsters_mock_client(comment_responses)
            fetched = collector.collect_comments(engine, config.lobsters, config.settings, log, batch_limit=10)

        assert fetched == 1

        # Step 3: verify
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.comments_json is not None
            comments = json.loads(disc.comments_json)
            assert len(comments) == 2
            assert comments[0]["comment"] == "Lobsters first!"
            assert comments[1]["comment"] == "Lobsters second!"
            assert disc.comment_count == 2

            assert disc.comments_status == "done"

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

    def test_rss_pipeline_creates_full_chain(self, engine):
        config = AppConfig(
            rss=RssConfig(sources=[RssSource(name="Blog", url="https://blog.example.com/feed.xml")]),
            settings=Settings(),
        )
        log = MagicMock()

        # Step 1: Collect RSS posts
        entry = _rss_entry(
            id="rss-1",
            title="Great Article",
            link="https://blog.example.com/great-article",
            summary="A teaser summary",
        )
        feed = _rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            rss = RssCollector()
            count = rss.collect(engine, config.rss, config.settings, log)

        assert count == 1

        # Verify BronzeDiscussion exists
        with engine.connect() as conn:
            bronze = conn.execute(sa.select(BronzeDiscussion)).fetchall()
            assert len(bronze) == 1
            assert bronze[0].source_type == "rss"
            assert bronze[0].external_id == "rss-1"

        # Verify SilverDiscussion exists with content_id
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc is not None
            assert disc.title == "Great Article"
            assert disc.content_id is not None

            # Verify SilverContent exists in pending state
            content = conn.execute(
                sa.select(SilverContent).where(SilverContent.id == disc.content_id)
            ).fetchone()
            assert content is not None
            assert content.fetch_status == "pending"
            assert "blog.example.com" in content.canonical_url

        # Step 2: RSS has no comments, skip

        # Step 3: Download pending content
        mock_resp = MagicMock()
        mock_resp.text = "<html><body><p>Full article body here</p></body></html>"
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        with patch("aggre.content_fetcher.httpx.Client", return_value=mock_client):
            downloaded = download_content(engine, config, log)

        assert downloaded == 1

        # Verify intermediate state: downloaded but not yet extracted
        with engine.connect() as conn:
            content = conn.execute(sa.select(SilverContent)).fetchone()
            assert content.fetch_status == "downloaded"
            assert content.raw_html is not None

        # Step 4: Extract text from downloaded HTML
        with patch("aggre.content_fetcher.trafilatura.extract", return_value="Full article body here"), \
             patch("aggre.content_fetcher.trafilatura.metadata.extract_metadata") as mock_meta:
            mock_meta_obj = MagicMock()
            mock_meta_obj.title = "Great Article - Full"
            mock_meta.return_value = mock_meta_obj

            extracted = extract_html_text(engine, config, log)

        assert extracted == 1

        # Verify full chain: BronzeDiscussion -> SilverDiscussion -> SilverContent
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.content_id is not None

            content = conn.execute(
                sa.select(SilverContent).where(SilverContent.id == disc.content_id)
            ).fetchone()
            assert content.fetch_status == "fetched"
            assert content.body_text == "Full article body here"
            assert content.title == "Great Article - Full"
            assert content.fetched_at is not None

            # Bronze post is linked
            bp = conn.execute(
                sa.select(BronzeDiscussion).where(BronzeDiscussion.id == disc.bronze_discussion_id)
            ).fetchone()
            assert bp is not None
            assert bp.source_type == "rss"

    def test_reddit_pipeline_with_comments(self, engine):
        """Reddit collect -> collect_comments -> verify discussion with comments."""
        config = AppConfig(
            reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
            settings=Settings(reddit_rate_limit=0.0),
        )
        log = MagicMock()
        collector = RedditCollector()

        # Step 1: collect
        post = _reddit_post()
        listing = _reddit_listing(post)
        post_responses = {"hot.json": listing, "new.json": listing}

        with patch("aggre.collectors.reddit.collector.httpx.Client") as mock_cls, \
             patch("aggre.collectors.reddit.collector.time.sleep"):
            mock_cls.return_value = MagicMock(get=MagicMock(side_effect=_reddit_fake_get(post_responses)))
            count = collector.collect(engine, config.reddit, config.settings, log)

        assert count == 1

        # Step 2: collect_comments
        c1 = _reddit_comment(comment_id="c1", body="Top comment")
        comment_resp = _reddit_comment_listing(c1)
        comment_responses = {"comments/abc123.json": comment_resp}

        with patch("aggre.collectors.reddit.collector.httpx.Client") as mock_cls, \
             patch("aggre.collectors.reddit.collector.time.sleep"):
            mock_cls.return_value = MagicMock(get=MagicMock(side_effect=_reddit_fake_get(comment_responses)))
            fetched = collector.collect_comments(engine, config.reddit, config.settings, log, batch_limit=10)

        assert fetched == 1

        # Verify full state
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.title == "Reddit Post"
            assert disc.source_type == "reddit"
            assert disc.comments_json is not None
            assert disc.comment_count == 1

            assert disc.comments_status == "done"

            bronze = conn.execute(sa.select(BronzeDiscussion)).fetchone()
            assert bronze is not None
            assert disc.bronze_discussion_id == bronze.id


# ===========================================================================
# Part 3: Content fetcher integration
# ===========================================================================


class TestContentFetcherIntegration:
    """Content fetcher: pending -> downloaded -> fetched/skipped/failed."""

    def _seed(self, engine, url, domain=None, fetch_status="pending"):
        with engine.begin() as conn:
            stmt = pg_insert(SilverContent).values(
                canonical_url=url,
                domain=domain,
                fetch_status=fetch_status,
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
            result = conn.execute(stmt)
            return result.inserted_primary_key[0]

    def test_download_then_extract_populates_fields(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        self._seed(engine, "https://example.com/article-1", domain="example.com")
        self._seed(engine, "https://example.com/article-2", domain="example.com")

        mock_resp = MagicMock()
        mock_resp.text = "<html><body>Content</body></html>"
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp

        with patch("aggre.content_fetcher.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 2

        # Verify intermediate state
        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).order_by(SilverContent.id)).fetchall()
            for row in rows:
                assert row.fetch_status == "downloaded"
                assert row.raw_html == "<html><body>Content</body></html>"

        with patch("aggre.content_fetcher.trafilatura.extract", return_value="Extracted text"), \
             patch("aggre.content_fetcher.trafilatura.metadata.extract_metadata") as mock_meta:
            meta_obj = MagicMock()
            meta_obj.title = "Article Title"
            mock_meta.return_value = meta_obj

            count = extract_html_text(engine, config, log)

        assert count == 2

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).order_by(SilverContent.id)
            ).fetchall()
            for row in rows:
                assert row.fetch_status == "fetched"
                assert row.body_text == "Extracted text"
                assert row.title == "Article Title"
                assert row.fetched_at is not None

    def test_youtube_urls_skipped(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        self._seed(engine, "https://youtube.com/watch?v=abc", domain="youtube.com")
        self._seed(engine, "https://youtu.be/xyz", domain="youtu.be")

        count = download_content(engine, config, log)
        assert count == 2

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent)).fetchall()
            for row in rows:
                assert row.fetch_status == "skipped"

    def test_failed_download_stores_error(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        self._seed(engine, "https://broken.example.com/page", domain="broken.example.com")

        mock_client = MagicMock()
        mock_client.get.side_effect = Exception("Connection timeout")

        with patch("aggre.content_fetcher.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "failed"
            assert "Connection timeout" in row.fetch_error
            assert row.fetched_at is not None

    def test_mixed_statuses(self, engine):
        """One normal, one YouTube (skip), one failing -- download step only."""
        config = AppConfig(settings=Settings())
        log = MagicMock()

        self._seed(engine, "https://example.com/good", domain="example.com")
        self._seed(engine, "https://youtube.com/watch?v=vid1", domain="youtube.com")
        self._seed(engine, "https://bad.example.com/broken", domain="bad.example.com")

        def side_effect_get(url):
            if "bad.example.com" in url:
                raise Exception("DNS failure")
            resp = MagicMock()
            resp.text = "<html><body>Good content</body></html>"
            resp.status_code = 200
            resp.headers = {"content-type": "text/html"}
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = MagicMock()
        mock_client.get.side_effect = side_effect_get

        with patch("aggre.content_fetcher.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 3

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).order_by(SilverContent.id)
            ).fetchall()

            # good article — downloaded (not yet extracted)
            assert rows[0].fetch_status == "downloaded"
            assert rows[0].raw_html == "<html><body>Good content</body></html>"

            # youtube skipped
            assert rows[1].fetch_status == "skipped"

            # broken site
            assert rows[2].fetch_status == "failed"
            assert "DNS failure" in rows[2].fetch_error

        # Now extract the downloaded one
        with patch("aggre.content_fetcher.trafilatura.extract", return_value="Good body"), \
             patch("aggre.content_fetcher.trafilatura.metadata.extract_metadata") as mock_meta:
            meta_obj = MagicMock()
            meta_obj.title = "Good Title"
            mock_meta.return_value = meta_obj

            count = extract_html_text(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).order_by(SilverContent.id)
            ).fetchall()

            assert rows[0].fetch_status == "fetched"
            assert rows[0].body_text == "Good body"
            assert rows[0].title == "Good Title"

            assert rows[1].fetch_status == "skipped"
            assert rows[2].fetch_status == "failed"

    def test_already_fetched_not_reprocessed(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        self._seed(engine, "https://example.com/done", domain="example.com", fetch_status="fetched")

        count = download_content(engine, config, log)
        assert count == 0
