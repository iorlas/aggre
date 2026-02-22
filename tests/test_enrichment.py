"""Tests for the URL enrichment module."""

from __future__ import annotations

from unittest.mock import MagicMock

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.config import AppConfig, HackernewsSource, LobstersSource, RssSource, Settings
from aggre.db import SilverContent
from aggre.enrichment import enrich_content_discussions


def _make_config() -> AppConfig:
    return AppConfig(
        hackernews=[HackernewsSource()],
        lobsters=[LobstersSource()],
        rss=[RssSource(name="Test", url="https://example.com/feed")],
        settings=Settings(hn_rate_limit=0.0, lobsters_rate_limit=0.0),
    )


def _seed_content(engine, url: str, enriched_at: str | None = None):
    """Insert a SilverContent row that can be enriched."""
    with engine.begin() as conn:
        stmt = pg_insert(SilverContent).values(
            canonical_url=url,
            domain="example.com",
            fetch_status="fetched",
            enriched_at=enriched_at,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
        conn.execute(stmt)


class TestEnrichment:
    def test_enriches_content(self, engine):
        config = _make_config()
        log = MagicMock()

        _seed_content(engine, "https://example.com/article")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 2

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        results = enrich_content_discussions(
            engine, config, log, batch_limit=50,
            hn_collector=mock_hn, lobsters_collector=mock_lob,
        )

        assert results == {"hackernews": 2, "lobsters": 1, "processed": 1}

        mock_hn.search_by_url.assert_called_once_with(
            "https://example.com/article", engine, config, log
        )
        mock_lob.search_by_url.assert_called_once_with(
            "https://example.com/article", engine, config, log
        )

        # Check enriched_at was set on SilverContent
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.canonical_url == "https://example.com/article"
                )
            ).fetchone()
            assert row.enriched_at is not None

    def test_skips_already_enriched(self, engine):
        config = _make_config()
        log = MagicMock()

        _seed_content(engine, "https://example.com/old", enriched_at="2024-01-01T00:00:00Z")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        results = enrich_content_discussions(
            engine, config, log, batch_limit=50,
            hn_collector=mock_hn, lobsters_collector=mock_lob,
        )

        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
        mock_hn.search_by_url.assert_not_called()
        mock_lob.search_by_url.assert_not_called()

    def test_respects_batch_limit(self, engine):
        config = _make_config()
        log = MagicMock()

        # Create 5 content rows
        for i in range(5):
            _seed_content(engine, f"https://example.com/{i}")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        results = enrich_content_discussions(
            engine, config, log, batch_limit=3,
            hn_collector=mock_hn, lobsters_collector=mock_lob,
        )

        # Should only process 3
        assert mock_hn.search_by_url.call_count == 3
        assert mock_lob.search_by_url.call_count == 3
        assert results["processed"] == 3

    def test_handles_search_failure_gracefully(self, engine):
        config = _make_config()
        log = MagicMock()

        _seed_content(engine, "https://example.com/fail")

        mock_hn = MagicMock()
        mock_hn.search_by_url.side_effect = Exception("HN API error")

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        results = enrich_content_discussions(
            engine, config, log, batch_limit=50,
            hn_collector=mock_hn, lobsters_collector=mock_lob,
        )

        # HN failed but lobsters succeeded
        assert results == {"hackernews": 0, "lobsters": 1, "processed": 1}

        # Content should NOT be marked as enriched (will be retried next batch)
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.canonical_url == "https://example.com/fail"
                )
            ).fetchone()
            assert row.enriched_at is None

    def test_no_pending_returns_zeros(self, engine):
        config = _make_config()
        log = MagicMock()

        mock_hn = MagicMock()
        mock_lob = MagicMock()

        results = enrich_content_discussions(
            engine, config, log, batch_limit=50,
            hn_collector=mock_hn, lobsters_collector=mock_lob,
        )
        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
