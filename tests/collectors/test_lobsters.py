"""Tests for the Lobsters collector."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource
from aggre.config import AppConfig
from aggre.db import SilverDiscussion
from aggre.settings import Settings
from tests.factories import (
    lobsters_comment,
    lobsters_story,
    lobsters_story_detail,
    make_config,
    seed_content,
    seed_discussion,
)
from tests.helpers import collect, get_discussions, get_sources

pytestmark = pytest.mark.integration


class TestLobstersCollectorDiscussions:
    def test_stores_posts(self, engine, mock_http):
        story = lobsters_story()
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[story])  # same story, should dedup

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=1))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 1

        items = get_discussions(engine)
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
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=1))
            count1 = collect(LobstersCollector(), engine, config.lobsters, config.settings)
            count2 = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count1 == 1
        assert count2 == 1  # collect_discussions returns all API items; dedup is in upsert

    def test_multiple_stories(self, engine, mock_http):
        story1 = lobsters_story(short_id="aaa", title="First")
        story2 = lobsters_story(short_id="bbb", title="Second")
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story1])
        mock_http.get(url__regex=r"newest\.json").respond(json=[story2])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=1))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 2

    def test_tag_filtering(self, engine, mock_http):
        story = lobsters_story()
        rust_route = mock_http.get(url__regex=r"t/rust\.json").respond(json=[story])
        python_route = mock_http.get(url__regex=r"t/python\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters", tags=["rust", "python"])], pages=1))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 1
        # Should use tag URLs instead of hottest/newest
        assert rust_route.call_count == 1
        assert python_route.call_count == 1
        # Verify hottest/newest were NOT called (no routes registered for them)
        called_urls = [str(call.request.url) for call in mock_http.calls]
        assert not any("hottest.json" in u for u in called_urls)

    def test_no_config_returns_zero(self, engine):
        config = AppConfig(lobsters=LobstersConfig(sources=[], pages=1), settings=Settings(lobsters_rate_limit=0.0))
        collector = LobstersCollector()
        assert collect(collector, engine, config.lobsters, config.settings) == 0

    def test_paginates_multiple_pages(self, engine, mock_http):
        """Collector fetches multiple pages when config.pages > 1."""
        story_p1 = lobsters_story(short_id="page1")
        story_p2 = lobsters_story(short_id="page2")

        mock_http.get(url__regex=r"hottest\.json\?page=1").respond(json=[story_p1])
        mock_http.get(url__regex=r"hottest\.json\?page=2").respond(json=[story_p2])
        mock_http.get(url__regex=r"newest\.json\?page=1").respond(json=[])
        mock_http.get(url__regex=r"newest\.json\?page=2").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=2))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 2

    def test_tag_urls_paginated(self, engine, mock_http):
        """Tag URLs are also paginated."""
        story = lobsters_story()

        mock_http.get(url__regex=r"t/rust\.json\?page=1").respond(json=[story])
        mock_http.get(url__regex=r"t/rust\.json\?page=2").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters", tags=["rust"])], pages=2))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 1


class TestLobstersCollectorFetchDiscussionComments:
    def test_sets_comments_fetched_at_on_success(self, engine, mock_http):
        config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=1))
        collector = LobstersCollector()

        content_id = seed_content(engine, "https://example.com/lob-fetch-test", domain="example.com")
        discussion_id = seed_discussion(engine, source_type="lobsters", external_id="abc123", content_id=content_id)

        comment = lobsters_comment(short_id="com1", comment="Nice!")
        detail = lobsters_story_detail(short_id="abc123", comments=[comment])
        mock_http.get(url__regex=r"s/abc123\.json").respond(json=detail)

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            collector.fetch_discussion_comments(engine, discussion_id, "abc123", None, config.settings)

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverDiscussion.comments_fetched_at).where(SilverDiscussion.id == discussion_id)).first()
        assert row.comments_fetched_at is not None


class TestLobstersSource:
    def test_creates_source_row(self, engine, mock_http):
        mock_http.get(url__regex=r"hottest\.json").respond(json=[])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=1))
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "lobsters"
        assert rows[0].name == "Lobsters"

    def test_reuses_existing_source(self, engine, mock_http):
        mock_http.get(url__regex=r"hottest\.json").respond(json=[])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=1))
            collect(LobstersCollector(), engine, config.lobsters, config.settings)
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert len(get_sources(engine)) == 1
