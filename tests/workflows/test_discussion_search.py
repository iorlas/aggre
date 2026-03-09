"""Tests for per-item discussion search (search_one).

Uses real PostgreSQL engine for DB queries, mocks search collectors.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aggre.workflows.discussion_search import DISCUSSION_SEARCH_SKIP_DOMAINS, search_one
from tests.factories import make_config, seed_content

pytestmark = pytest.mark.integration


class TestSearchOne:
    def test_returns_skipped_for_nonexistent_content(self, engine):
        config = make_config()

        result = search_one(engine, config, 99999)

        assert result == "skipped"

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

        assert result == "searched"
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

        # Partial success — lobsters worked
        assert result == "searched"

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

        assert result == "searched"

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
        assert result == "searched"


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
