"""Tests for the RSS collector."""

from __future__ import annotations

from unittest.mock import patch

import sqlalchemy as sa

from aggre.collectors.rss import RssCollector
from aggre.config import AppConfig, RssSource
from aggre.db import Base, BronzePost, SilverPost, Source


def _make_engine():
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _make_config(*rss_sources):
    return AppConfig(rss=list(rss_sources))


def _fake_entry(**kwargs):
    """Build a dict-like object that supports both attribute access and .get().

    Real feedparser entries are FeedParserDict (a dict subclass with attribute access),
    so our fake must also support dict() conversion.
    """
    defaults = {
        "id": "entry-1",
        "title": "Test Post",
        "link": "https://example.com/1",
        "author": "Alice",
        "summary": "Hello world",
        "published": "2025-01-01T00:00:00Z",
    }
    defaults.update(kwargs)
    # Filter out None values so entry.get("id") returns None (missing key) vs literal None
    data = {k: v for k, v in defaults.items() if v is not None}

    class Entry(dict):
        """Mimics feedparser's FeedParserDict: a dict with attribute access."""

        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError:
                return None

    return Entry(data)


def _fake_feed(entries, feed_title="Test Feed"):
    feed_meta = {"title": feed_title}

    class FeedMeta:
        def get(self, key, default=None):
            return feed_meta.get(key, default)

    class Feed:
        def __init__(self, entries, feed):
            self.entries = entries
            self.feed = feed

    return Feed(entries, FeedMeta())


class TestRssCollector:
    def _log(self):
        import structlog

        return structlog.get_logger()

    def test_new_items_stored(self):
        engine = _make_engine()
        config = _make_config(RssSource(name="Test Blog", url="https://example.com/feed.xml"))

        entry = _fake_entry(
            id="post-1",
            title="First Post",
            link="https://example.com/post-1",
            author="Bob",
            summary="Content here",
            published="2025-06-01T12:00:00Z",
        )
        feed = _fake_feed([entry])

        with patch("aggre.collectors.rss.feedparser.parse", return_value=feed) as mock_parse:
            collector = RssCollector()
            count = collector.collect(engine, config, self._log())

        assert count == 1
        mock_parse.assert_called_once_with("https://example.com/feed.xml")

        with engine.connect() as conn:
            # Check bronze_posts
            rows = conn.execute(sa.select(BronzePost)).fetchall()
            assert len(rows) == 1
            assert rows[0].source_type == "rss"
            assert rows[0].external_id == "post-1"

            # Check silver_posts
            rows = conn.execute(sa.select(SilverPost)).fetchall()
            assert len(rows) == 1
            assert rows[0].title == "First Post"
            assert rows[0].author == "Bob"
            assert rows[0].url == "https://example.com/post-1"
            assert rows[0].content_text == "Content here"
            assert rows[0].published_at == "2025-06-01T12:00:00Z"
            assert rows[0].source_type == "rss"
            assert rows[0].external_id == "post-1"
            assert rows[0].bronze_post_id is not None

    def test_duplicate_items_skipped(self):
        engine = _make_engine()
        config = _make_config(RssSource(name="Test Blog", url="https://example.com/feed.xml"))

        entry = _fake_entry(id="post-1", title="First Post")
        feed = _fake_feed([entry])

        with patch("aggre.collectors.rss.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count1 = collector.collect(engine, config, self._log())
            count2 = collector.collect(engine, config, self._log())

        assert count1 == 1
        assert count2 == 0

        with engine.connect() as conn:
            raw_count = conn.execute(sa.select(sa.func.count()).select_from(BronzePost)).scalar()
            content_count = conn.execute(sa.select(sa.func.count()).select_from(SilverPost)).scalar()
            assert raw_count == 1
            assert content_count == 1

    def test_source_row_created(self):
        engine = _make_engine()
        config = _make_config(RssSource(name="My Feed", url="https://example.com/rss"))

        feed = _fake_feed([])

        with patch("aggre.collectors.rss.feedparser.parse", return_value=feed):
            collector = RssCollector()
            collector.collect(engine, config, self._log())

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "rss"
            assert rows[0].name == "My Feed"

    def test_source_row_reused_on_second_run(self):
        engine = _make_engine()
        config = _make_config(RssSource(name="My Feed", url="https://example.com/rss"))

        feed = _fake_feed([])

        with patch("aggre.collectors.rss.feedparser.parse", return_value=feed):
            collector = RssCollector()
            collector.collect(engine, config, self._log())
            collector.collect(engine, config, self._log())

        with engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(Source)).scalar()
            assert count == 1

    def test_last_fetched_at_updated(self):
        engine = _make_engine()
        config = _make_config(RssSource(name="My Feed", url="https://example.com/rss"))

        feed = _fake_feed([])

        with patch("aggre.collectors.rss.feedparser.parse", return_value=feed):
            collector = RssCollector()
            collector.collect(engine, config, self._log())

        with engine.connect() as conn:
            row = conn.execute(sa.select(Source.last_fetched_at)).fetchone()
            assert row[0] is not None

    def test_multiple_entries(self):
        engine = _make_engine()
        config = _make_config(RssSource(name="Blog", url="https://example.com/feed"))

        entries = [
            _fake_entry(id="a", title="Post A", link="https://example.com/a"),
            _fake_entry(id="b", title="Post B", link="https://example.com/b"),
            _fake_entry(id="c", title="Post C", link="https://example.com/c"),
        ]
        feed = _fake_feed(entries)

        with patch("aggre.collectors.rss.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count = collector.collect(engine, config, self._log())

        assert count == 3

        with engine.connect() as conn:
            raw_count = conn.execute(sa.select(sa.func.count()).select_from(BronzePost)).scalar()
            content_count = conn.execute(sa.select(sa.func.count()).select_from(SilverPost)).scalar()
            assert raw_count == 3
            assert content_count == 3

    def test_entry_uses_link_as_fallback_id(self):
        engine = _make_engine()
        config = _make_config(RssSource(name="Blog", url="https://example.com/feed"))

        entry = _fake_entry(id=None, link="https://example.com/post-42")
        feed = _fake_feed([entry])

        with patch("aggre.collectors.rss.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count = collector.collect(engine, config, self._log())

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverPost.external_id)).fetchone()
            assert row[0] == "https://example.com/post-42"

    def test_multiple_feeds(self):
        engine = _make_engine()
        config = _make_config(
            RssSource(name="Feed A", url="https://a.com/feed"),
            RssSource(name="Feed B", url="https://b.com/feed"),
        )

        feed_a = _fake_feed([_fake_entry(id="a1", title="A1")])
        feed_b = _fake_feed([_fake_entry(id="b1", title="B1")])

        def mock_parse(url):
            if url == "https://a.com/feed":
                return feed_a
            return feed_b

        with patch("aggre.collectors.rss.feedparser.parse", side_effect=mock_parse):
            collector = RssCollector()
            count = collector.collect(engine, config, self._log())

        assert count == 2

        with engine.connect() as conn:
            source_count = conn.execute(sa.select(sa.func.count()).select_from(Source)).scalar()
            assert source_count == 2
