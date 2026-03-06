"""Tests for the LessWrong collector."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from aggre.collectors.lesswrong.collector import LesswrongCollector
from aggre.collectors.lesswrong.config import LesswrongConfig, LesswrongSource
from aggre.db import SilverContent, SilverDiscussion
from aggre.settings import Settings
from tests.factories import lesswrong_graphql_response, lesswrong_post
from tests.helpers import collect, get_discussions, get_sources

pytestmark = pytest.mark.integration


class TestLesswrongCollector:
    def test_empty_sources_returns_zero(self, engine):
        config = LesswrongConfig(sources=[])
        settings = Settings()
        collector = LesswrongCollector()
        assert collect(collector, engine, config, settings) == 0

    def test_stores_posts(self, engine, mock_http):
        config = LesswrongConfig(sources=[LesswrongSource(name="LW Frontpage", min_karma=0)])
        settings = Settings()

        post = lesswrong_post()
        mock_http.post("https://www.lesswrong.com/graphql").respond(
            json=lesswrong_graphql_response(post),
        )

        with patch("aggre.collectors.lesswrong.collector.time.sleep"):
            collector = LesswrongCollector()
            count = collect(collector, engine, config, settings)

        assert count == 1

        rows = get_discussions(engine)
        assert len(rows) == 1
        assert rows[0].source_type == "lesswrong"
        assert rows[0].external_id == "abc123lw"
        assert rows[0].title == "Test LW Post"
        assert rows[0].author == "Test Author"
        assert rows[0].score == 42
        assert rows[0].comment_count == 5

    def test_http_failure_continues(self, engine, mock_http):
        """GraphQL POST fails → logs, continues."""
        config = LesswrongConfig(sources=[LesswrongSource(name="LW Frontpage", min_karma=0)])
        settings = Settings()

        mock_http.post("https://www.lesswrong.com/graphql").mock(
            side_effect=Exception("Connection refused"),
        )

        with patch("aggre.collectors.lesswrong.collector.time.sleep"):
            collector = LesswrongCollector()
            count = collect(collector, engine, config, settings)

        assert count == 0

    def test_min_karma_filter(self, engine, mock_http):
        """Post below min_karma threshold → skipped."""
        config = LesswrongConfig(sources=[LesswrongSource(name="LW Frontpage", min_karma=20)])
        settings = Settings()

        low_karma = lesswrong_post(post_id="low1", base_score=5)
        high_karma = lesswrong_post(post_id="high1", base_score=50)
        mock_http.post("https://www.lesswrong.com/graphql").respond(
            json=lesswrong_graphql_response(low_karma, high_karma),
        )

        with patch("aggre.collectors.lesswrong.collector.time.sleep"):
            collector = LesswrongCollector()
            count = collect(collector, engine, config, settings)

        assert count == 1
        rows = get_discussions(engine)
        assert len(rows) == 1
        assert rows[0].external_id == "high1"

    def test_link_post_creates_external_content(self, engine, mock_http):
        """Link post (has url field) → content points to external URL."""
        config = LesswrongConfig(sources=[LesswrongSource(name="LW Frontpage", min_karma=0)])
        settings = Settings()

        post = lesswrong_post(url="https://example.com/external-article")
        mock_http.post("https://www.lesswrong.com/graphql").respond(
            json=lesswrong_graphql_response(post),
        )

        with patch("aggre.collectors.lesswrong.collector.time.sleep"):
            collector = LesswrongCollector()
            collect(collector, engine, config, settings)

        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.content_id is not None
            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == disc.content_id)).fetchone()
            assert content.canonical_url == "https://example.com/external-article"

    def test_native_essay_creates_page_content(self, engine, mock_http):
        """Native essay (no url field) → content points to LW page URL."""
        config = LesswrongConfig(sources=[LesswrongSource(name="LW Frontpage", min_karma=0)])
        settings = Settings()

        post = lesswrong_post(url=None, page_url="https://www.lesswrong.com/posts/abc123lw/test-lw-post")
        mock_http.post("https://www.lesswrong.com/graphql").respond(
            json=lesswrong_graphql_response(post),
        )

        with patch("aggre.collectors.lesswrong.collector.time.sleep"):
            collector = LesswrongCollector()
            collect(collector, engine, config, settings)

        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.content_id is not None
            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == disc.content_id)).fetchone()
            assert "lesswrong.com" in content.canonical_url

    def test_meta_contains_tags_af_votecount(self, engine, mock_http):
        """Verify tags, AF flag, vote count are stored in meta."""
        config = LesswrongConfig(sources=[LesswrongSource(name="LW Frontpage", min_karma=0)])
        settings = Settings()

        post = lesswrong_post(
            af=True,
            vote_count=99,
            tags=[{"name": "AI safety"}, {"name": "alignment"}],
        )
        mock_http.post("https://www.lesswrong.com/graphql").respond(
            json=lesswrong_graphql_response(post),
        )

        with patch("aggre.collectors.lesswrong.collector.time.sleep"):
            collector = LesswrongCollector()
            collect(collector, engine, config, settings)

        rows = get_discussions(engine)
        meta = json.loads(rows[0].meta)
        assert meta["af"] is True
        assert meta["vote_count"] == 99
        assert "AI safety" in meta["tags"]
        assert "alignment" in meta["tags"]

    def test_source_row_created(self, engine, mock_http):
        config = LesswrongConfig(sources=[LesswrongSource(name="LW Frontpage", min_karma=0)])
        settings = Settings()

        mock_http.post("https://www.lesswrong.com/graphql").respond(
            json=lesswrong_graphql_response(),
        )

        with patch("aggre.collectors.lesswrong.collector.time.sleep"):
            collector = LesswrongCollector()
            collect(collector, engine, config, settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "lesswrong"

    def test_empty_post_id_skipped(self, engine, mock_http):
        """Post with empty _id → skipped during process_discussion."""
        config = LesswrongConfig(sources=[LesswrongSource(name="LW Frontpage", min_karma=0)])
        settings = Settings()

        post = lesswrong_post(post_id="")
        mock_http.post("https://www.lesswrong.com/graphql").respond(
            json=lesswrong_graphql_response(post),
        )

        with patch("aggre.collectors.lesswrong.collector.time.sleep"):
            collector = LesswrongCollector()
            count = collect(collector, engine, config, settings)

        # collect_discussions skips empty post_id, so count is 0
        assert count == 0
