"""Tests for the Hacker News collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

from aggre.collectors.hackernews import HackernewsCollector
from aggre.config import AppConfig, HackernewsConfig, HackernewsSource
from aggre.settings import Settings
from aggre.db import BronzeDiscussion, SilverDiscussion, Source


def _make_config(rate_limit: float = 0.0) -> AppConfig:
    return AppConfig(
        hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
        settings=Settings(hn_rate_limit=rate_limit),
    )


def _make_hit(object_id: str = "12345", title: str = "Test Story", author: str = "pg", url: str = "https://example.com/article"):
    return {
        "objectID": object_id,
        "title": title,
        "author": author,
        "url": url,
        "points": 100,
        "num_comments": 25,
        "created_at": "2024-01-15T12:00:00.000Z",
    }


def _make_search_response(*hits):
    return {"hits": list(hits)}


def _make_item_response(object_id: str = "12345", children: list | None = None):
    return {
        "id": int(object_id),
        "children": children or [],
    }


def _make_comment_child(
    comment_id: int = 100, author: str = "commenter", text: str = "Great article!",
    points: int = 5, children: list | None = None,
):
    return {
        "id": comment_id,
        "author": author,
        "text": text,
        "points": points,
        "parent_id": 12345,
        "created_at": "2024-01-15T13:00:00.000Z",
        "children": children or [],
    }


def _mock_httpx_client(responses: dict):
    """Create a mock httpx.Client that returns configured responses based on URL patterns."""
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


class TestHackernewsCollectorDiscussions:
    def test_stores_posts(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        hit = _make_hit()
        responses = {"search_by_date": _make_search_response(hit)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            count = collector.collect(engine, config.hackernews, config.settings, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(BronzeDiscussion)).fetchall()
            assert len(raws) == 1
            assert raws[0].external_id == "12345"
            assert raws[0].source_type == "hackernews"

            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].title == "Test Story"
            assert items[0].author == "pg"
            assert items[0].source_type == "hackernews"
            assert items[0].url == "https://example.com/article"

            assert items[0].score == 100
            assert items[0].comment_count == 25
            assert items[0].comments_status == "pending"

            meta = json.loads(items[0].meta)
            assert "hn_url" in meta

    def test_dedup_same_story(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        hit = _make_hit()
        responses = {"search_by_date": _make_search_response(hit)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            count1 = collector.collect(engine, config.hackernews, config.settings, log)
            count2 = collector.collect(engine, config.hackernews, config.settings, log)

        assert count1 == 1
        assert count2 == 0

        with engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(BronzeDiscussion)).scalar() == 1
            assert conn.execute(sa.select(sa.func.count()).select_from(SilverDiscussion)).scalar() == 1

    def test_multiple_stories(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        hit1 = _make_hit(object_id="111", title="First")
        hit2 = _make_hit(object_id="222", title="Second")
        responses = {"search_by_date": _make_search_response(hit1, hit2)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            count = collector.collect(engine, config.hackernews, config.settings, log)

        assert count == 2

    def test_story_without_url_uses_hn_url(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        hit = _make_hit(object_id="999")
        hit["url"] = None  # Ask HN / Show HN with no external URL
        responses = {"search_by_date": _make_search_response(hit)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.hackernews, config.settings, log)

        with engine.connect() as conn:
            item = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert item.url == "https://news.ycombinator.com/item?id=999"

    def test_no_config_returns_zero(self, engine):
        config = AppConfig(hackernews=HackernewsConfig(sources=[]), settings=Settings(hn_rate_limit=0.0))
        log = MagicMock()
        collector = HackernewsCollector()
        assert collector.collect(engine, config.hackernews, config.settings, log) == 0


class TestHackernewsCollectorComments:
    def test_fetches_comments_and_marks_done(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        # First, collect a story
        hit = _make_hit()
        responses = {"search_by_date": _make_search_response(hit)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.hackernews, config.settings, log)

        # Now fetch comments
        comment = _make_comment_child(comment_id=100, text="Nice!")
        item_response = _make_item_response(object_id="12345", children=[comment])
        comment_responses = {"items/12345": item_response}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            fetched = collector.collect_comments(engine, config.hackernews, config.settings, log, batch_limit=10)

        assert fetched == 1

        with engine.connect() as conn:
            # Verify comments stored as JSON on SilverDiscussion
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].comments_json is not None
            comments_data = json.loads(items[0].comments_json)
            assert len(comments_data) == 1
            assert comments_data[0]["author"] == "commenter"
            assert comments_data[0]["text"] == "Nice!"
            assert items[0].comment_count == 1

            # HN collector updates comments_status column directly
            assert items[0].comments_status == "done"

    def test_nested_comments(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        hit = _make_hit()
        responses = {"search_by_date": _make_search_response(hit)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.hackernews, config.settings, log)

        reply = _make_comment_child(comment_id=200, text="I agree", children=[])
        parent = _make_comment_child(comment_id=100, text="Top level", children=[reply])
        item_response = _make_item_response(object_id="12345", children=[parent])
        comment_responses = {"items/12345": item_response}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            collector.collect_comments(engine, config.hackernews, config.settings, log, batch_limit=10)

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert items[0].comments_json is not None
            comments_data = json.loads(items[0].comments_json)
            # Top-level has 1 child (parent comment)
            assert len(comments_data) == 1
            assert comments_data[0]["text"] == "Top level"
            # Nested reply is inside children
            assert len(comments_data[0]["children"]) == 1
            assert comments_data[0]["children"][0]["text"] == "I agree"

    def test_no_pending_returns_zero(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()
        assert collector.collect_comments(engine, config.hackernews, config.settings, log, batch_limit=10) == 0

    def test_zero_batch_returns_zero(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()
        assert collector.collect_comments(engine, config.hackernews, config.settings, log, batch_limit=0) == 0

    def test_respects_batch_limit(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        # Collect 3 stories
        hits = [_make_hit(object_id=str(i), title=f"Story {i}") for i in range(3)]
        responses = {"search_by_date": _make_search_response(*hits)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.hackernews, config.settings, log)

        # Fetch comments with batch_limit=2
        comment_responses = {
            f"items/{i}": _make_item_response(object_id=str(i), children=[])
            for i in range(3)
        }

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            fetched = collector.collect_comments(engine, config.hackernews, config.settings, log, batch_limit=2)

        assert fetched == 2

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            # HN uses comments_status column directly
            statuses = [i.comments_status for i in items]
            assert statuses.count("done") == 2
            assert statuses.count("pending") == 1


class TestHackernewsSearchByUrl:
    def test_search_finds_and_stores(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        hit = _make_hit(object_id="42", url="https://example.com/article")
        responses = {"search?query": _make_search_response(hit)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            found = collector.search_by_url("https://example.com/article", engine, config.hackernews, config.settings, log)

        assert found == 1

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].source_type == "hackernews"

    def test_search_dedup(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        hit = _make_hit(object_id="42")
        responses = {"search?query": _make_search_response(hit)}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            found1 = collector.search_by_url("https://example.com", engine, config.hackernews, config.settings, log)
            found2 = collector.search_by_url("https://example.com", engine, config.hackernews, config.settings, log)

        assert found1 == 1
        assert found2 == 0

    def test_search_no_results(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        responses = {"search?query": {"hits": []}}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            found = collector.search_by_url("https://no-results.com", engine, config.hackernews, config.settings, log)

        assert found == 0


class TestHackernewsSource:
    def test_creates_source_row(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        responses = {"search_by_date": _make_search_response()}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.hackernews, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "hackernews"
            assert rows[0].name == "Hacker News"

    def test_reuses_existing_source(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HackernewsCollector()

        responses = {"search_by_date": _make_search_response()}

        with patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls, \
             patch("aggre.collectors.hackernews.collector.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.hackernews, config.settings, log)
            collector.collect(engine, config.hackernews, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
