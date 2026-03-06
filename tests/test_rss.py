"""Tests for the RSS collector."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa

from aggre.collectors.rss.collector import RssCollector
from aggre.collectors.rss.config import RssConfig, RssSource
from aggre.db import SilverDiscussion, Source
from tests.factories import make_config, rss_entry, rss_feed
from tests.helpers import collect, get_discussions, get_sources

pytestmark = pytest.mark.integration


class TestRssCollector:
    def test_new_items_stored(self, engine):
        config = make_config(rss=RssConfig(sources=[RssSource(name="Test Blog", url="https://example.com/feed.xml")]))

        entry = rss_entry(
            id="post-1",
            title="First Post",
            link="https://example.com/post-1",
            author="Bob",
            summary="Content here",
            published="2025-06-01T12:00:00Z",
        )
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed) as mock_parse:
            collector = RssCollector()
            count = collect(collector, engine, config.rss, config.settings)

        assert count == 1
        mock_parse.assert_called_once_with("https://example.com/feed.xml")

        # Check silver_discussions
        rows = get_discussions(engine)
        assert len(rows) == 1
        assert rows[0].title == "First Post"
        assert rows[0].author == "Bob"
        assert rows[0].url == "https://example.com/post-1"
        assert rows[0].content_text == "Content here"
        assert rows[0].published_at == "2025-06-01T12:00:00Z"
        assert rows[0].source_type == "rss"
        assert rows[0].external_id == "post-1"

    def test_duplicate_items_skipped(self, engine):
        config = make_config(rss=RssConfig(sources=[RssSource(name="Test Blog", url="https://example.com/feed.xml")]))

        entry = rss_entry(id="post-1", title="First Post")
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count1 = collect(collector, engine, config.rss, config.settings)
            count2 = collect(collector, engine, config.rss, config.settings)

        assert count1 == 1
        assert count2 == 1  # collect_discussions returns all API items; dedup is in upsert

        assert len(get_discussions(engine)) == 1

    def test_source_row_created(self, engine):
        config = make_config(rss=RssConfig(sources=[RssSource(name="My Feed", url="https://example.com/rss")]))

        feed = rss_feed([])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            collect(collector, engine, config.rss, config.settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "rss"
        assert rows[0].name == "My Feed"

    def test_source_row_reused_on_second_run(self, engine):
        config = make_config(rss=RssConfig(sources=[RssSource(name="My Feed", url="https://example.com/rss")]))

        feed = rss_feed([])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            collect(collector, engine, config.rss, config.settings)
            collect(collector, engine, config.rss, config.settings)

        assert len(get_sources(engine)) == 1

    def test_last_fetched_at_updated(self, engine):
        config = make_config(rss=RssConfig(sources=[RssSource(name="My Feed", url="https://example.com/rss")]))

        feed = rss_feed([rss_entry()])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            collect(collector, engine, config.rss, config.settings)

        with engine.connect() as conn:
            row = conn.execute(sa.select(Source.last_fetched_at)).fetchone()
            assert row[0] is not None

    def test_multiple_entries(self, engine):
        config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))

        entries = [
            rss_entry(id="a", title="Post A", link="https://example.com/a"),
            rss_entry(id="b", title="Post B", link="https://example.com/b"),
            rss_entry(id="c", title="Post C", link="https://example.com/c"),
        ]
        feed = rss_feed(entries)

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count = collect(collector, engine, config.rss, config.settings)

        assert count == 3

        assert len(get_discussions(engine)) == 3

    def test_entry_uses_link_as_fallback_id(self, engine):
        config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))

        entry = rss_entry(id=None, link="https://example.com/post-42")
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count = collect(collector, engine, config.rss, config.settings)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverDiscussion.external_id)).fetchone()
            assert row[0] == "https://example.com/post-42"

    def test_bozo_feed_continues(self, engine):
        """Bozo feed with entries → warning logged, entries still processed."""
        config = make_config(rss=RssConfig(sources=[RssSource(name="Bad Feed", url="https://example.com/bad.xml")]))

        entry = rss_entry(id="bozo-1", title="Bozo Post")
        feed = rss_feed([entry])
        feed.bozo = True
        feed.bozo_exception = Exception("malformed XML")

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count = collect(collector, engine, config.rss, config.settings)

        assert count == 1
        assert len(get_discussions(engine)) == 1

    def test_entry_no_id_no_link_skipped(self, engine):
        """Entry with no id and no link → skipped."""
        config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))

        entry = rss_entry(id=None, link=None)
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count = collect(collector, engine, config.rss, config.settings)

        assert count == 0
        assert len(get_discussions(engine)) == 0

    def test_content_fallback_to_content_field(self, engine):
        """Entry with no summary but has content[0]["value"] → uses that for content_text."""
        config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))

        entry = rss_entry(id="content-1", summary=None)
        entry["content"] = [{"value": "Full article body from content field"}]
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collector = RssCollector()
            count = collect(collector, engine, config.rss, config.settings)

        assert count == 1
        rows = get_discussions(engine)
        assert rows[0].content_text == "Full article body from content field"

    def test_multiple_feeds(self, engine):
        config = make_config(
            rss=RssConfig(
                sources=[
                    RssSource(name="Feed A", url="https://a.com/feed"),
                    RssSource(name="Feed B", url="https://b.com/feed"),
                ]
            )
        )

        feed_a = rss_feed([rss_entry(id="a1", title="A1")])
        feed_b = rss_feed([rss_entry(id="b1", title="B1")])

        def mock_parse(url):
            if url == "https://a.com/feed":
                return feed_a
            return feed_b

        with patch("aggre.collectors.rss.collector.feedparser.parse", side_effect=mock_parse):
            collector = RssCollector()
            count = collect(collector, engine, config.rss, config.settings)

        assert count == 2

        assert len(get_sources(engine)) == 2
