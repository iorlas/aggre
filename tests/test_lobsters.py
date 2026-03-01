"""Tests for the Lobsters collector."""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource
from aggre.config import AppConfig
from aggre.settings import Settings
from tests.factories import (
    lobsters_comment,
    lobsters_story,
    lobsters_story_detail,
    make_config,
)
from tests.helpers import collect, get_observations, get_sources

pytestmark = pytest.mark.integration


class TestLobstersCollectorDiscussions:
    def test_stores_posts(self, engine, mock_http):
        story = lobsters_story()
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[story])  # same story, should dedup

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 1

        items = get_observations(engine)
        assert len(items) == 1
        assert items[0].title == "Test Story"
        assert items[0].author == "testuser"
        assert items[0].source_type == "lobsters"
        assert items[0].url == "https://example.com/article"

        assert items[0].score == 10
        assert items[0].comment_count == 3
        assert items[0].comments_json is None  # pending: no comments fetched yet

        meta = json.loads(items[0].meta)
        assert "tags" in meta
        assert "lobsters_url" in meta

    def test_dedup_across_runs(self, engine, mock_http):
        story = lobsters_story()
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            count1 = collect(LobstersCollector(), engine, config.lobsters, config.settings)
            count2 = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count1 == 1
        assert count2 == 1  # collect_references returns all API items; dedup is in upsert

    def test_multiple_stories(self, engine, mock_http):
        story1 = lobsters_story(short_id="aaa", title="First")
        story2 = lobsters_story(short_id="bbb", title="Second")
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story1])
        mock_http.get(url__regex=r"newest\.json").respond(json=[story2])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 2

    def test_tag_filtering(self, engine, mock_http):
        story = lobsters_story()
        rust_route = mock_http.get(url__regex=r"t/rust\.json").respond(json=[story])
        python_route = mock_http.get(url__regex=r"t/python\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters", tags=["rust", "python"])]))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 1
        # Should use tag URLs instead of hottest/newest
        assert rust_route.call_count == 1
        assert python_route.call_count == 1
        # Verify hottest/newest were NOT called (no routes registered for them)
        called_urls = [str(call.request.url) for call in mock_http.calls]
        assert not any("hottest.json" in u for u in called_urls)

    def test_no_config_returns_zero(self, engine):
        config = AppConfig(lobsters=LobstersConfig(sources=[]), settings=Settings(lobsters_rate_limit=0.0))
        collector = LobstersCollector()
        assert collect(collector, engine, config.lobsters, config.settings) == 0


class TestLobstersCollectorComments:
    def test_fetches_comments_and_marks_done(self, engine, mock_http):
        # Collect a story first
        story = lobsters_story()
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        # Reset mock_http for comment fetching
        mock_http.reset()

        # Now fetch comments
        comment = lobsters_comment(short_id="com1", comment="Nice!")
        detail = lobsters_story_detail(short_id="abc123", comments=[comment])
        mock_http.get(url__regex=r"s/abc123\.json").respond(json=detail)

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            collector = LobstersCollector()
            fetched = collector.collect_comments(engine, config.lobsters, config.settings, batch_limit=10)

        assert fetched == 1

        # Verify comments stored as JSON on SilverObservation
        items = get_observations(engine)
        assert len(items) == 1
        assert items[0].comments_json is not None
        comments_data = json.loads(items[0].comments_json)
        assert len(comments_data) == 1
        assert comments_data[0]["commenting_user"]["username"] == "commenter"
        assert comments_data[0]["comment"] == "Nice!"
        assert items[0].comment_count == 1

        # Comments have been fetched
        assert items[0].comments_json is not None

    def test_indent_levels(self, engine, mock_http):
        story = lobsters_story()
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        # Reset mock_http for comment fetching
        mock_http.reset()

        parent = lobsters_comment(short_id="c1", comment="Parent", indent_level=1)
        child = lobsters_comment(short_id="c2", comment="Child", indent_level=2, parent_comment="c1")
        detail = lobsters_story_detail(short_id="abc123", comments=[parent, child])
        mock_http.get(url__regex=r"s/abc123\.json").respond(json=detail)

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            collector = LobstersCollector()
            collector.collect_comments(engine, config.lobsters, config.settings, batch_limit=10)

        items = get_observations(engine)
        assert items[0].comments_json is not None
        comments_data = json.loads(items[0].comments_json)
        assert len(comments_data) == 2
        assert comments_data[0]["comment"] == "Parent"
        assert comments_data[0]["indent_level"] == 1
        assert comments_data[1]["comment"] == "Child"
        assert comments_data[1]["indent_level"] == 2
        assert comments_data[1]["parent_comment"] == "c1"

    def test_no_pending_returns_zero(self, engine):
        config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
        collector = LobstersCollector()
        assert collector.collect_comments(engine, config.lobsters, config.settings, batch_limit=10) == 0

    def test_zero_batch_returns_zero(self, engine):
        config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
        collector = LobstersCollector()
        assert collector.collect_comments(engine, config.lobsters, config.settings, batch_limit=0) == 0

    def test_respects_batch_limit(self, engine, mock_http):
        stories = [lobsters_story(short_id=f"s{i}", title=f"Story {i}") for i in range(3)]
        mock_http.get(url__regex=r"hottest\.json").respond(json=stories)
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        # Reset mock_http for comment fetching
        mock_http.reset()

        for i in range(3):
            detail = lobsters_story_detail(short_id=f"s{i}", comments=[])
            mock_http.get(url__regex=rf"s/s{i}\.json").respond(json=detail)

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            collector = LobstersCollector()
            fetched = collector.collect_comments(engine, config.lobsters, config.settings, batch_limit=2)

        assert fetched == 2

        items = get_observations(engine)
        done = [i for i in items if i.comments_json is not None]
        pending = [i for i in items if i.comments_json is None]
        assert len(done) == 2
        assert len(pending) == 1


class TestLobstersSearchByUrl:
    def test_search_finds_and_stores(self, engine, mock_http):
        story = lobsters_story(short_id="found1", url="https://example.com/article")
        mock_http.get(url__regex=r"domains/example\.com\.json").respond(json=[story])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collector = LobstersCollector()
            found = collector.search_by_url("https://example.com/article", engine, config.lobsters, config.settings)

        assert found == 1

        items = get_observations(engine)
        assert len(items) == 1
        assert items[0].source_type == "lobsters"

    def test_search_filters_by_exact_url(self, engine, mock_http):
        story_match = lobsters_story(short_id="match", url="https://example.com/target")
        story_other = lobsters_story(short_id="other", url="https://example.com/other")
        mock_http.get(url__regex=r"domains/example\.com\.json").respond(json=[story_match, story_other])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collector = LobstersCollector()
            found = collector.search_by_url("https://example.com/target", engine, config.lobsters, config.settings)

        assert found == 1

    def test_search_dedup(self, engine, mock_http):
        story = lobsters_story(short_id="dup1", url="https://example.com/article")
        mock_http.get(url__regex=r"domains/example\.com\.json").respond(json=[story])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collector = LobstersCollector()
            found1 = collector.search_by_url("https://example.com/article", engine, config.lobsters, config.settings)
            found2 = collector.search_by_url("https://example.com/article", engine, config.lobsters, config.settings)

        assert found1 == 1
        assert found2 == 1  # search_by_url returns all API items; dedup is in upsert

    def test_search_caches_domain_lookups(self, engine, mock_http):
        story1 = lobsters_story(short_id="s1", url="https://example.com/article-1")
        story2 = lobsters_story(short_id="s2", url="https://example.com/article-2")
        domain_route = mock_http.get(url__regex=r"domains/example\.com\.json").respond(json=[story1, story2])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collector = LobstersCollector()
            found1 = collector.search_by_url("https://example.com/article-1", engine, config.lobsters, config.settings)
            found2 = collector.search_by_url("https://example.com/article-2", engine, config.lobsters, config.settings)

        assert found1 == 1
        assert found2 == 1
        # Only 1 HTTP request — second call uses cached domain data
        assert domain_route.call_count == 1

    def test_search_no_domain_returns_zero(self, engine):
        config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
        collector = LobstersCollector()
        assert collector.search_by_url("not-a-url", engine, config.lobsters, config.settings) == 0

    def test_search_caches_429_response(self, engine, mock_http, caplog):
        domain_route = mock_http.get(url__regex=r"domains/example\.com\.json").respond(status_code=429)

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collector = LobstersCollector()
            with caplog.at_level(logging.WARNING, logger="aggre.collectors.lobsters.collector"):
                found1 = collector.search_by_url("https://example.com/article-1", engine, config.lobsters, config.settings)
                found2 = collector.search_by_url("https://example.com/article-2", engine, config.lobsters, config.settings)

        assert found1 == 0
        assert found2 == 0
        # Only 1 HTTP request — second call uses cached empty result from 429
        assert domain_route.call_count == 1
        assert any(r.levelno >= logging.WARNING for r in caplog.records)


class TestLobstersSource:
    def test_creates_source_row(self, engine, mock_http):
        mock_http.get(url__regex=r"hottest\.json").respond(json=[])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "lobsters"
        assert rows[0].name == "Lobsters"

    def test_reuses_existing_source(self, engine, mock_http):
        mock_http.get(url__regex=r"hottest\.json").respond(json=[])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
            collect(LobstersCollector(), engine, config.lobsters, config.settings)
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert len(get_sources(engine)) == 1
