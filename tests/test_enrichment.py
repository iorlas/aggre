"""Tests for the URL enrichment module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa

from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource
from aggre.dagster_defs.enrichment.job import enrich_content_discussions
from aggre.stages.model import StageTracking
from aggre.stages.status import Stage, StageStatus
from aggre.stages.tracking import upsert_done
from tests.factories import make_config, seed_content

pytestmark = pytest.mark.integration


class TestEnrichment:
    def test_enriches_content(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/article", domain="example.com", text="article text")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 2

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        results = enrich_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        assert results == {"hackernews": 2, "lobsters": 1, "processed": 1}

        mock_hn.search_by_url.assert_called_once_with("https://example.com/article", engine, config.hackernews, config.settings)
        mock_lob.search_by_url.assert_called_once_with("https://example.com/article", engine, config.lobsters, config.settings)

        # Check enrichment tracking was set
        with engine.connect() as conn:
            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/article",
                    StageTracking.stage == Stage.ENRICH,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.DONE

    def test_skips_already_enriched(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/old", domain="example.com")
        upsert_done(engine, "content", "https://example.com/old", Stage.ENRICH)

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        results = enrich_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
        mock_hn.search_by_url.assert_not_called()
        mock_lob.search_by_url.assert_not_called()

    def test_respects_batch_limit(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        # Create 5 content rows with text (enrichment requires text IS NOT NULL)
        for i in range(5):
            seed_content(engine, f"https://example.com/{i}", domain="example.com", text=f"article {i}")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        results = enrich_content_discussions(
            engine,
            config,
            batch_limit=3,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        # Should only process 3
        assert mock_hn.search_by_url.call_count == 3
        assert mock_lob.search_by_url.call_count == 3
        assert results["processed"] == 3

    def test_handles_search_failure_gracefully(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/fail", domain="example.com", text="fail article text")

        mock_hn = MagicMock()
        mock_hn.search_by_url.side_effect = Exception("HN API error")

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        results = enrich_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        # HN failed but lobsters succeeded
        assert results == {"hackernews": 0, "lobsters": 1, "processed": 1}

        # Content should be marked as failed (will be retried next batch)
        with engine.connect() as conn:
            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/fail",
                    StageTracking.stage == Stage.ENRICH,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.FAILED

    def test_no_pending_returns_zeros(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        mock_hn = MagicMock()
        mock_lob = MagicMock()

        results = enrich_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )
        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
