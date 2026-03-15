"""Tests for per-item discussion search (search_one).

Uses real PostgreSQL engine for DB queries, mocks search collectors.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from aggre.db import SilverContent
from aggre.workflows.discussion_search import DISCUSSION_SEARCH_SKIP_DOMAINS, search_one
from tests.factories import make_config, seed_content

pytestmark = pytest.mark.integration


class TestSearchOne:
    def test_returns_skipped_for_nonexistent_content(self, engine):
        config = make_config()

        result = search_one(engine, config, 99999)

        assert result.status == "skipped"
        assert result.reason == "not_found"

    def test_searches_hn_and_lobsters(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/article", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 2

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        result = search_one(
            engine,
            config,
            content_id,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        assert result.status == "searched"
        mock_hn.search_by_url.assert_called_once_with(
            "https://example.com/article",
            engine,
            config.hackernews,
            config.settings,
        )
        mock_lob.search_by_url.assert_called_once_with(
            "https://example.com/article",
            engine,
            config.lobsters,
            config.settings,
        )

    def test_partial_success_when_hn_fails(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/partial", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.side_effect = Exception("HN API error")

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        result = search_one(
            engine,
            config,
            content_id,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        # Partial success — lobsters worked but degraded
        assert result.status == "searched_partial"
        assert "hackernews_error" in result.detail

    def test_partial_success_when_lobsters_fails(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/partial2", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 3

        mock_lob = MagicMock()
        mock_lob.search_by_url.side_effect = Exception("Lobsters down")

        result = search_one(
            engine,
            config,
            content_id,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        assert result.status == "searched_partial"
        assert "lobsters_error" in result.detail

    def test_raises_when_both_fail(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/both-fail", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.side_effect = Exception("HN down")

        mock_lob = MagicMock()
        mock_lob.search_by_url.side_effect = Exception("Lobsters down")

        with pytest.raises(Exception, match="HN down"):
            search_one(
                engine,
                config,
                content_id,
                hn_collector=mock_hn,
                lobsters_collector=mock_lob,
            )

    def test_searched_with_zero_results(self, engine):
        """Returns 'searched' even when both collectors find zero discussions."""
        config = make_config()
        content_id = seed_content(engine, "https://example.com/defaults", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0
        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        result = search_one(
            engine,
            config,
            content_id,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )
        assert result.status == "searched"

    def test_sets_discussions_searched_at_on_success(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/timestamp-test", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0
        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        search_one(engine, config, content_id, hn_collector=mock_hn, lobsters_collector=mock_lob)

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(SilverContent.discussions_searched_at).where(SilverContent.id == content_id)
            ).first()
        assert row.discussions_searched_at is not None

    def test_sets_discussions_searched_at_on_partial_success(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/partial-ts", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.side_effect = Exception("HN down")
        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        search_one(engine, config, content_id, hn_collector=mock_hn, lobsters_collector=mock_lob)

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(SilverContent.discussions_searched_at).where(SilverContent.id == content_id)
            ).first()
        assert row.discussions_searched_at is not None

    def test_no_discussions_searched_at_when_both_fail(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/both-fail-ts", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.side_effect = Exception("HN down")
        mock_lob = MagicMock()
        mock_lob.search_by_url.side_effect = Exception("Lobsters down")

        with pytest.raises(Exception):
            search_one(engine, config, content_id, hn_collector=mock_hn, lobsters_collector=mock_lob)

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(SilverContent.discussions_searched_at).where(SilverContent.id == content_id)
            ).first()
        assert row.discussions_searched_at is None


class TestSkipDomains:
    """Verify the skip domain set contains expected entries."""

    def test_youtube_domains_skipped(self):
        assert "youtube.com" in DISCUSSION_SEARCH_SKIP_DOMAINS
        assert "m.youtube.com" in DISCUSSION_SEARCH_SKIP_DOMAINS
        assert "youtu.be" in DISCUSSION_SEARCH_SKIP_DOMAINS

    def test_reddit_domains_skipped(self):
        assert "reddit.com" in DISCUSSION_SEARCH_SKIP_DOMAINS
        assert "old.reddit.com" in DISCUSSION_SEARCH_SKIP_DOMAINS

    def test_linkedin_skipped(self):
        assert "linkedin.com" in DISCUSSION_SEARCH_SKIP_DOMAINS
