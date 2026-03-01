"""Tests for the Hacker News collector."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource
from aggre.db import SilverContent, SilverDiscussion
from tests.factories import (
    hn_comment_child,
    hn_hit,
    hn_item_response,
    hn_search_response,
    make_config,
)
from tests.helpers import collect, get_discussions, get_sources

pytestmark = pytest.mark.integration


class TestHackernewsCollectorDiscussions:
    def test_stores_posts(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            count = collect(collector, engine, config.hackernews, config.settings)

        assert count == 1

        items = get_discussions(engine)
        assert len(items) == 1
        assert items[0].title == "Test Story"
        assert items[0].author == "pg"
        assert items[0].source_type == "hackernews"
        assert items[0].url == "https://example.com/article"

        assert items[0].score == 100
        assert items[0].comment_count == 25
        assert items[0].comments_json is None  # pending: no comments fetched yet

        meta = json.loads(items[0].meta)
        assert "hn_url" in meta

    def test_dedup_same_story(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            count1 = collect(collector, engine, config.hackernews, config.settings)
            count2 = collect(collector, engine, config.hackernews, config.settings)

        assert count1 == 1
        assert count2 == 1  # collect_discussions returns refs regardless; dedup is in upsert

        assert len(get_discussions(engine)) == 1

    def test_multiple_stories(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit1 = hn_hit(object_id="111", title="First")
        hit2 = hn_hit(object_id="222", title="Second")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit1, hit2),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            count = collect(collector, engine, config.hackernews, config.settings)

        assert count == 2

    def test_story_without_url_creates_self_post_content(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit(object_id="999", url=None, story_text="This is a self-post with some text content.")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        with engine.connect() as conn:
            item = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert item.url == "https://news.ycombinator.com/item?id=999"
            # Self-posts now create SilverContent with text populated
            assert item.content_id is not None

            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == item.content_id)).fetchone()
            assert content is not None
            assert content.text == "This is a self-post with some text content."

    def test_no_config_returns_zero(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()
        assert collect(collector, engine, config.hackernews, config.settings) == 0


class TestHackernewsCollectorComments:
    def test_fetches_comments_and_marks_done(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        # First, collect a story
        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        # Now fetch comments
        comment = hn_comment_child(comment_id=100, text="Nice!")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/items/12345").respond(
            json=hn_item_response(object_id="12345", children=[comment]),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            fetched = collector.collect_comments(engine, config.hackernews, config.settings, batch_limit=10)

        assert fetched == 1

        # Verify comments stored as JSON on SilverDiscussion
        items = get_discussions(engine)
        assert len(items) == 1
        assert items[0].comments_json is not None
        comments_data = json.loads(items[0].comments_json)
        assert len(comments_data) == 1
        assert comments_data[0]["author"] == "commenter"
        assert comments_data[0]["text"] == "Nice!"
        assert items[0].comment_count == 1

        # Comments have been fetched
        assert items[0].comments_json is not None

    def test_nested_comments(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        reply = hn_comment_child(comment_id=200, text="I agree", children=[])
        parent = hn_comment_child(comment_id=100, text="Top level", children=[reply])
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/items/12345").respond(
            json=hn_item_response(object_id="12345", children=[parent]),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collector.collect_comments(engine, config.hackernews, config.settings, batch_limit=10)

        items = get_discussions(engine)
        assert items[0].comments_json is not None
        comments_data = json.loads(items[0].comments_json)
        # Top-level has 1 child (parent comment)
        assert len(comments_data) == 1
        assert comments_data[0]["text"] == "Top level"
        # Nested reply is inside children
        assert len(comments_data[0]["children"]) == 1
        assert comments_data[0]["children"][0]["text"] == "I agree"

    def test_no_pending_returns_zero(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()
        assert collector.collect_comments(engine, config.hackernews, config.settings, batch_limit=10) == 0

    def test_zero_batch_returns_zero(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()
        assert collector.collect_comments(engine, config.hackernews, config.settings, batch_limit=0) == 0

    def test_respects_batch_limit(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        # Collect 3 stories
        hits = [hn_hit(object_id=str(i), title=f"Story {i}") for i in range(3)]
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(*hits),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        # Fetch comments with batch_limit=2 — set up routes for all 3 stories
        for i in range(3):
            mock_http.get(url__startswith=f"https://hn.algolia.com/api/v1/items/{i}").respond(
                json=hn_item_response(object_id=str(i), children=[]),
            )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            fetched = collector.collect_comments(engine, config.hackernews, config.settings, batch_limit=2)

        assert fetched == 2

        items = get_discussions(engine)
        done = [i for i in items if i.comments_json is not None]
        pending = [i for i in items if i.comments_json is None]
        assert len(done) == 2
        assert len(pending) == 1


class TestHackernewsSearchByUrl:
    def test_search_finds_and_stores(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit(object_id="42", url="https://example.com/article")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search?query=").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            found = collector.search_by_url("https://example.com/article", engine, config.hackernews, config.settings)

        assert found == 1

        items = get_discussions(engine)
        assert len(items) == 1
        assert items[0].source_type == "hackernews"

    def test_search_dedup(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit(object_id="42")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search?query=").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            found1 = collector.search_by_url("https://example.com", engine, config.hackernews, config.settings)
            found2 = collector.search_by_url("https://example.com", engine, config.hackernews, config.settings)

        assert found1 == 1
        assert found2 == 1  # search_by_url always returns hit count, dedup is in upsert

    def test_search_no_results(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search?query=").respond(
            json={"hits": []},
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            found = collector.search_by_url("https://no-results.com", engine, config.hackernews, config.settings)

        assert found == 0


class TestHackernewsSource:
    def test_creates_source_row(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "hackernews"
        assert rows[0].name == "Hacker News"

    def test_reuses_existing_source(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)
            collect(collector, engine, config.hackernews, config.settings)

        assert len(get_sources(engine)) == 1
