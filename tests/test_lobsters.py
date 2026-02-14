"""Tests for the Lobsters collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

from aggre.collectors.lobsters import LobstersCollector
from aggre.config import AppConfig, LobstersSource, Settings
from aggre.db import Base, BronzeComment, BronzePost, SilverComment, SilverPost, Source


def _make_engine():
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _make_config(tags: list[str] | None = None, rate_limit: float = 0.0) -> AppConfig:
    return AppConfig(
        lobsters=[LobstersSource(name="Lobsters", tags=tags or [])],
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


class TestLobstersCollectorPosts:
    def test_stores_posts(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story()
        responses = {
            "hottest.json": [story],
            "newest.json": [story],  # same story, should dedup
        }

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            count = collector.collect(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(BronzePost)).fetchall()
            assert len(raws) == 1
            assert raws[0].external_id == "abc123"
            assert raws[0].source_type == "lobsters"

            items = conn.execute(sa.select(SilverPost)).fetchall()
            assert len(items) == 1
            assert items[0].title == "Test Story"
            assert items[0].author == "testuser"
            assert items[0].source_type == "lobsters"
            assert items[0].url == "https://example.com/article"

            meta = json.loads(items[0].meta)
            assert meta["score"] == 10
            assert meta["comment_count"] == 3
            assert meta["comments_status"] == "pending"

    def test_dedup_across_runs(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story()
        responses = {"hottest.json": [story], "newest.json": []}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            count1 = collector.collect(engine, config, log)
            count2 = collector.collect(engine, config, log)

        assert count1 == 1
        assert count2 == 0

    def test_multiple_stories(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story1 = _make_story(short_id="aaa", title="First")
        story2 = _make_story(short_id="bbb", title="Second")
        responses = {"hottest.json": [story1], "newest.json": [story2]}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            count = collector.collect(engine, config, log)

        assert count == 2

    def test_tag_filtering(self):
        engine = _make_engine()
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

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = tracking_client(responses)
            count = collector.collect(engine, config, log)

        assert count == 1
        # Should use tag URLs instead of hottest/newest
        assert any("t/rust.json" in u for u in requested_urls)
        assert any("t/python.json" in u for u in requested_urls)
        assert not any("hottest.json" in u for u in requested_urls)

    def test_no_config_returns_zero(self):
        engine = _make_engine()
        config = AppConfig(settings=Settings(lobsters_rate_limit=0.0))
        log = MagicMock()
        collector = LobstersCollector()
        assert collector.collect(engine, config, log) == 0


class TestLobstersCollectorComments:
    def test_fetches_comments_and_marks_done(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        # Collect a story first
        story = _make_story()
        responses = {"hottest.json": [story], "newest.json": []}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config, log)

        # Now fetch comments
        comment = _make_comment(short_id="com1", comment="Nice!")
        detail = _make_story_detail(short_id="abc123", comments=[comment])
        comment_responses = {"s/abc123.json": detail}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            fetched = collector.collect_comments(engine, config, log, batch_limit=10)

        assert fetched == 1

        with engine.connect() as conn:
            rcs = conn.execute(sa.select(BronzeComment)).fetchall()
            assert len(rcs) == 1
            assert rcs[0].external_id == "com1"

            comments = conn.execute(sa.select(SilverComment)).fetchall()
            assert len(comments) == 1
            assert comments[0].author == "commenter"
            assert comments[0].body == "Nice!"
            assert comments[0].depth == 0  # indent_level 1 → depth 0

            items = conn.execute(sa.select(SilverPost)).fetchall()
            meta = json.loads(items[0].meta)
            assert meta["comments_status"] == "done"

    def test_indent_levels(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story()
        responses = {"hottest.json": [story], "newest.json": []}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config, log)

        parent = _make_comment(short_id="c1", comment="Parent", indent_level=1)
        child = _make_comment(short_id="c2", comment="Child", indent_level=2, parent_comment="c1")
        detail = _make_story_detail(short_id="abc123", comments=[parent, child])
        comment_responses = {"s/abc123.json": detail}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            collector.collect_comments(engine, config, log, batch_limit=10)

        with engine.connect() as conn:
            comments = conn.execute(sa.select(SilverComment).order_by(SilverComment.depth)).fetchall()
            assert len(comments) == 2
            assert comments[0].depth == 0
            assert comments[0].body == "Parent"
            assert comments[1].depth == 1
            assert comments[1].body == "Child"
            assert comments[1].parent_id == "c1"

    def test_no_pending_returns_zero(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()
        assert collector.collect_comments(engine, config, log, batch_limit=10) == 0

    def test_zero_batch_returns_zero(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()
        assert collector.collect_comments(engine, config, log, batch_limit=0) == 0

    def test_respects_batch_limit(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        stories = [_make_story(short_id=f"s{i}", title=f"Story {i}") for i in range(3)]
        responses = {"hottest.json": stories, "newest.json": []}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config, log)

        comment_responses = {
            f"s/s{i}.json": _make_story_detail(short_id=f"s{i}", comments=[])
            for i in range(3)
        }

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(comment_responses)
            fetched = collector.collect_comments(engine, config, log, batch_limit=2)

        assert fetched == 2

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverPost)).fetchall()
            statuses = [json.loads(i.meta).get("comments_status") for i in items]
            assert statuses.count("done") == 2
            assert statuses.count("pending") == 1


class TestLobstersSearchByUrl:
    def test_search_finds_and_stores(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story(short_id="found1", url="https://example.com/article")
        responses = {"domains/example.com.json": [story]}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            found = collector.search_by_url("https://example.com/article", engine, config, log)

        assert found == 1

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverPost)).fetchall()
            assert len(items) == 1
            assert items[0].source_type == "lobsters"

    def test_search_filters_by_exact_url(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story_match = _make_story(short_id="match", url="https://example.com/target")
        story_other = _make_story(short_id="other", url="https://example.com/other")
        responses = {"domains/example.com.json": [story_match, story_other]}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            found = collector.search_by_url("https://example.com/target", engine, config, log)

        assert found == 1

    def test_search_dedup(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        story = _make_story(short_id="dup1", url="https://example.com/article")
        responses = {"domains/example.com.json": [story]}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            found1 = collector.search_by_url("https://example.com/article", engine, config, log)
            found2 = collector.search_by_url("https://example.com/article", engine, config, log)

        assert found1 == 1
        assert found2 == 0

    def test_search_no_domain_returns_zero(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()
        assert collector.search_by_url("not-a-url", engine, config, log) == 0


class TestLobstersSource:
    def test_creates_source_row(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        responses = {"hottest.json": [], "newest.json": []}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "lobsters"
            assert rows[0].name == "Lobsters"

    def test_reuses_existing_source(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = LobstersCollector()

        responses = {"hottest.json": [], "newest.json": []}

        with patch("aggre.collectors.lobsters.httpx.Client") as mock_cls, \
             patch("aggre.collectors.lobsters.time.sleep"):
            mock_cls.return_value = _mock_httpx_client(responses)
            collector.collect(engine, config, log)
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
