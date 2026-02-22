"""Tests for the Lobsters collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource
from aggre.config import AppConfig
from aggre.db import BronzeDiscussion, SilverDiscussion, Source
from aggre.settings import Settings


def _make_config(tags: list[str] | None = None, rate_limit: float = 0.0) -> AppConfig:
    return AppConfig(
        lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters", tags=tags or [])]),
        settings=Settings(lobsters_rate_limit=rate_limit),
    )


def _make_story(
    short_id: str = "abc123",
    title: str = "Test Story",
    url: str = "https://example.com/article",
    score: int = 10,
    comment_count: int = 3,
    tags: list[str] | None = None,
    submitter_user: str = "testuser",
):
    return {
        "short_id": short_id,
        "title": title,
        "url": url,
        "score": score,
        "comment_count": comment_count,
        "tags": tags or ["programming"],
        "submitter_user": submitter_user,
        "created_at": "2024-01-15T12:00:00.000Z",
        "comments_url": f"https://lobste.rs/s/{short_id}",
    }


def _make_story_detail(short_id: str = "abc123", comments: list | None = None):
    story = _make_story(short_id=short_id)
    story["comments"] = comments or []
    return story


def _make_comment(
    short_id: str = "com1",
    comment: str = "Great article!",
    username: str = "commenter",
    score: int = 5,
    indent_level: int = 1,
    parent_comment: str | None = None,
):
    return {
        "short_id": short_id,
        "comment": comment,
        "commenting_user": {"username": username},
        "score": score,
        "indent_level": indent_level,
        "parent_comment": parent_comment,
        "created_at": "2024-01-15T13:00:00.000Z",
    }


def _mock_httpx_client(responses: dict):
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


class TestLobstersCollectorDiscussions:
    def test_stores_posts(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story()
        responses = {
            "hottest.json": [story],
            "newest.json": [story],  # same story, should dedup
        }

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            count = collector.collect(engine, config.lobsters, config.settings, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(BronzeDiscussion)).fetchall()
            assert len(raws) == 1
            assert raws[0].external_id == "abc123"
            assert raws[0].source_type == "lobsters"

            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].title == "Test Story"
            assert items[0].author == "testuser"
            assert items[0].source_type == "lobsters"
            assert items[0].url == "https://example.com/article"

            assert items[0].score == 10
            assert items[0].comment_count == 3
            assert items[0].comments_status == "pending"

            meta = json.loads(items[0].meta)
            assert "tags" in meta
            assert "lobsters_url" in meta

    def test_dedup_across_runs(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story()
        responses = {"hottest.json": [story], "newest.json": []}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            count1 = collector.collect(engine, config.lobsters, config.settings, log)
            count2 = collector.collect(engine, config.lobsters, config.settings, log)

        assert count1 == 1
        assert count2 == 0

    def test_multiple_stories(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story1 = _make_story(short_id="aaa", title="First")
        story2 = _make_story(short_id="bbb", title="Second")
        responses = {"hottest.json": [story1], "newest.json": [story2]}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            count = collector.collect(engine, config.lobsters, config.settings, log)

        assert count == 2

    def test_tag_filtering(self, engine):
        config = _make_config(tags=["rust", "python"])
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story()
        responses = {
            "t/rust.json": [story],
            "t/python.json": [],
        }

        requested_urls = []

        def tracking_client(responses):
            client = MagicMock()

            def fake_get(url):
                requested_urls.append(url)
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

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = tracking_client(responses)
            count = collector.collect(engine, config.lobsters, config.settings, log)

        assert count == 1
        # Should use tag URLs instead of hottest/newest
        assert any("t/rust.json" in u for u in requested_urls)
        assert any("t/python.json" in u for u in requested_urls)
        assert not any("hottest.json" in u for u in requested_urls)

    def test_no_config_returns_zero(self, engine):
        config = AppConfig(lobsters=LobstersConfig(sources=[]), settings=Settings(lobsters_rate_limit=0.0))
        log = MagicMock()
        collector = LobstersCollector()
        assert collector.collect(engine, config.lobsters, config.settings, log) == 0


class TestLobstersCollectorComments:
    def test_fetches_comments_and_marks_done(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        # Collect a story first
        story = _make_story()
        responses = {"hottest.json": [story], "newest.json": []}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.lobsters, config.settings, log)

        # Now fetch comments
        comment = _make_comment(short_id="com1", comment="Nice!")
        detail = _make_story_detail(short_id="abc123", comments=[comment])
        comment_responses = {"s/abc123.json": detail}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            fetched = collector.collect_comments(engine, config.lobsters, config.settings, log, batch_limit=10)

        assert fetched == 1

        with engine.connect() as conn:
            # Verify comments stored as JSON on SilverDiscussion
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].comments_json is not None
            comments_data = json.loads(items[0].comments_json)
            assert len(comments_data) == 1
            assert comments_data[0]["commenting_user"]["username"] == "commenter"
            assert comments_data[0]["comment"] == "Nice!"
            assert items[0].comment_count == 1

            # Lobsters stores comments_status as column
            assert items[0].comments_status == "done"

    def test_indent_levels(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story()
        responses = {"hottest.json": [story], "newest.json": []}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.lobsters, config.settings, log)

        parent = _make_comment(short_id="c1", comment="Parent", indent_level=1)
        child = _make_comment(short_id="c2", comment="Child", indent_level=2, parent_comment="c1")
        detail = _make_story_detail(short_id="abc123", comments=[parent, child])
        comment_responses = {"s/abc123.json": detail}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            collector.collect_comments(engine, config.lobsters, config.settings, log, batch_limit=10)

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert items[0].comments_json is not None
            comments_data = json.loads(items[0].comments_json)
            assert len(comments_data) == 2
            assert comments_data[0]["comment"] == "Parent"
            assert comments_data[0]["indent_level"] == 1
            assert comments_data[1]["comment"] == "Child"
            assert comments_data[1]["indent_level"] == 2
            assert comments_data[1]["parent_comment"] == "c1"

    def test_no_pending_returns_zero(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()
        assert collector.collect_comments(engine, config.lobsters, config.settings, log, batch_limit=10) == 0

    def test_zero_batch_returns_zero(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()
        assert collector.collect_comments(engine, config.lobsters, config.settings, log, batch_limit=0) == 0

    def test_respects_batch_limit(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        stories = [_make_story(short_id=f"s{i}", title=f"Story {i}") for i in range(3)]
        responses = {"hottest.json": stories, "newest.json": []}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.lobsters, config.settings, log)

        comment_responses = {f"s/s{i}.json": _make_story_detail(short_id=f"s{i}", comments=[]) for i in range(3)}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            fetched = collector.collect_comments(engine, config.lobsters, config.settings, log, batch_limit=2)

        assert fetched == 2

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            # comments_status is a column
            statuses = [i.comments_status for i in items]
            assert statuses.count("done") == 2
            assert statuses.count("pending") == 1


class TestLobstersSearchByUrl:
    def test_search_finds_and_stores(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story(short_id="found1", url="https://example.com/article")
        responses = {"domains/example.com.json": [story]}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            found = collector.search_by_url("https://example.com/article", engine, config.lobsters, config.settings, log)

        assert found == 1

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].source_type == "lobsters"

    def test_search_filters_by_exact_url(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story_match = _make_story(short_id="match", url="https://example.com/target")
        story_other = _make_story(short_id="other", url="https://example.com/other")
        responses = {"domains/example.com.json": [story_match, story_other]}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            found = collector.search_by_url("https://example.com/target", engine, config.lobsters, config.settings, log)

        assert found == 1

    def test_search_dedup(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story(short_id="dup1", url="https://example.com/article")
        responses = {"domains/example.com.json": [story]}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            found1 = collector.search_by_url("https://example.com/article", engine, config.lobsters, config.settings, log)
            found2 = collector.search_by_url("https://example.com/article", engine, config.lobsters, config.settings, log)

        assert found1 == 1
        assert found2 == 0

    def test_search_caches_domain_lookups(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story1 = _make_story(short_id="s1", url="https://example.com/article-1")
        story2 = _make_story(short_id="s2", url="https://example.com/article-2")
        responses = {"domains/example.com.json": [story1, story2]}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_client = _mock_httpx_client(responses)
            mock_cls.return_value = mock_client

            found1 = collector.search_by_url("https://example.com/article-1", engine, config.lobsters, config.settings, log)
            found2 = collector.search_by_url("https://example.com/article-2", engine, config.lobsters, config.settings, log)

        assert found1 == 1
        assert found2 == 1
        # Only 1 HTTP request — second call uses cached domain data
        assert mock_client.get.call_count == 1

    def test_search_no_domain_returns_zero(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()
        assert collector.search_by_url("not-a-url", engine, config.lobsters, config.settings, log) == 0

    def test_search_caches_429_response(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        mock_client = MagicMock()
        resp_429 = MagicMock()
        resp_429.status_code = 429
        mock_client.get.return_value = resp_429

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client", return_value=mock_client),
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            found1 = collector.search_by_url("https://example.com/article-1", engine, config.lobsters, config.settings, log)
            found2 = collector.search_by_url("https://example.com/article-2", engine, config.lobsters, config.settings, log)

        assert found1 == 0
        assert found2 == 0
        # Only 1 HTTP request — second call uses cached empty result from 429
        assert mock_client.get.call_count == 1
        log.warning.assert_called_once()


class TestLobstersSource:
    def test_creates_source_row(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        responses = {"hottest.json": [], "newest.json": []}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.lobsters, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "lobsters"
            assert rows[0].name == "Lobsters"

    def test_reuses_existing_source(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        responses = {"hottest.json": [], "newest.json": []}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config.lobsters, config.settings, log)
            collector.collect(engine, config.lobsters, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
