"""Tests for the URL enrichment module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource
from aggre.dagster_defs.enrichment.job import enrich_content_discussions
from aggre.db import SilverContent
from tests.factories import make_config, seed_content

pytestmark = pytest.mark.integration


class TestEnrichment:
    def test_enriches_content(self, engine, log):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/article", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 2

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        results = enrich_content_discussions(
            engine,
            config,
            log,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        assert results == {"hackernews": 2, "lobsters": 1, "processed": 1}

        mock_hn.search_by_url.assert_called_once_with("https://example.com/article", engine, config.hackernews, config.settings, log)
        mock_lob.search_by_url.assert_called_once_with("https://example.com/article", engine, config.lobsters, config.settings, log)

        # Check enriched_at was set on SilverContent
        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent).where(SilverContent.canonical_url == "https://example.com/article")).fetchone()
            assert row.enriched_at is not None

    def test_skips_already_enriched(self, engine, log):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/old", domain="example.com", enriched_at="2024-01-01T00:00:00Z")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        results = enrich_content_discussions(
            engine,
            config,
            log,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
        mock_hn.search_by_url.assert_not_called()
        mock_lob.search_by_url.assert_not_called()

    def test_respects_batch_limit(self, engine, log):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        # Create 5 content rows
        for i in range(5):
            seed_content(engine, f"https://example.com/{i}", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        results = enrich_content_discussions(
            engine,
            config,
            log,
            batch_limit=3,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        # Should only process 3
        assert mock_hn.search_by_url.call_count == 3
        assert mock_lob.search_by_url.call_count == 3
        assert results["processed"] == 3

    def test_handles_search_failure_gracefully(self, engine, log):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/fail", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.side_effect = Exception("HN API error")

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        results = enrich_content_discussions(
            engine,
            config,
            log,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        # HN failed but lobsters succeeded
        assert results == {"hackernews": 0, "lobsters": 1, "processed": 1}

        # Content should NOT be marked as enriched (will be retried next batch)
        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent).where(SilverContent.canonical_url == "https://example.com/fail")).fetchone()
            assert row.enriched_at is None

    def test_no_pending_returns_zeros(self, engine, log):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        mock_hn = MagicMock()
        mock_lob = MagicMock()

        results = enrich_content_discussions(
            engine,
            config,
            log,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )
        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
