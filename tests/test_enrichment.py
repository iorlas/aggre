"""Tests for the URL enrichment module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

from aggre.config import AppConfig, HackernewsSource, LobstersSource, RssSource, Settings
from aggre.db import Base, BronzePost, SilverPost, Source
from aggre.enrichment import enrich_posts


def _make_engine():
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _make_config() -> AppConfig:
    return AppConfig(
        hackernews=[HackernewsSource()],
        lobsters=[LobstersSource()],
        rss=[RssSource(name="Test", url="https://example.com/feed")],
        settings=Settings(hn_rate_limit=0.0, lobsters_rate_limit=0.0),
    )


def _seed_rss_post(engine, url: str, external_id: str, meta: dict | None = None):
    """Insert an RSS post that can be enriched."""
    with engine.begin() as conn:
        # Ensure source exists
        row = conn.execute(sa.select(Source.id).where(Source.type == "rss")).first()
        if row:
            source_id = row[0]
        else:
            result = conn.execute(
                sa.insert(Source).values(type="rss", name="Test", config="{}")
            )
            source_id = result.lastrowid

        # Insert bronze post
        result = conn.execute(
            sa.insert(BronzePost).values(
                source_type="rss", external_id=external_id, raw_data="{}"
            )
        )
        bronze_id = result.lastrowid

        conn.execute(
            sa.insert(SilverPost).values(
                source_id=source_id,
                bronze_post_id=bronze_id,
                source_type="rss",
                external_id=external_id,
                title=f"Post {external_id}",
                url=url,
                published_at="2024-01-15T12:00:00Z",
                meta=json.dumps(meta) if meta else None,
            )
        )


def _seed_hn_post(engine, external_id: str):
    """Insert a HN post — should NOT be enriched."""
    with engine.begin() as conn:
        row = conn.execute(sa.select(Source.id).where(Source.type == "hackernews")).first()
        if row:
            source_id = row[0]
        else:
            result = conn.execute(
                sa.insert(Source).values(type="hackernews", name="HN", config="{}")
            )
            source_id = result.lastrowid

        result = conn.execute(
            sa.insert(BronzePost).values(
                source_type="hackernews", external_id=external_id, raw_data="{}"
            )
        )
        bronze_id = result.lastrowid

        conn.execute(
            sa.insert(SilverPost).values(
                source_id=source_id,
                bronze_post_id=bronze_id,
                source_type="hackernews",
                external_id=external_id,
                title=f"HN Post {external_id}",
                url="https://example.com",
                meta=json.dumps({"comments_status": "pending"}),
            )
        )


class TestEnrichment:
    def test_enriches_rss_post(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()

        _seed_rss_post(engine, "https://example.com/article", "rss-1")

        with patch("aggre.enrichment.HackernewsCollector") as mock_hn_cls, \
             patch("aggre.enrichment.LobstersCollector") as mock_lob_cls:
            mock_hn = MagicMock()
            mock_hn.search_by_url.return_value = 2
            mock_hn_cls.return_value = mock_hn

            mock_lob = MagicMock()
            mock_lob.search_by_url.return_value = 1
            mock_lob_cls.return_value = mock_lob

            results = enrich_posts(engine, config, log, batch_limit=50)

        assert results == {"hackernews": 2, "lobsters": 1}

        mock_hn.search_by_url.assert_called_once_with(
            "https://example.com/article", engine, config, log
        )
        mock_lob.search_by_url.assert_called_once_with(
            "https://example.com/article", engine, config, log
        )

        # Check meta was updated
        with engine.connect() as conn:
            item = conn.execute(
                sa.select(SilverPost).where(SilverPost.external_id == "rss-1")
            ).fetchone()
            meta = json.loads(item.meta)
            assert "enriched_at" in meta
            assert meta["enrichment_results"] == {"hackernews": 2, "lobsters": 1}

    def test_skips_already_enriched(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()

        _seed_rss_post(
            engine,
            "https://example.com/old",
            "rss-old",
            meta={"enriched_at": "2024-01-01T00:00:00Z", "enrichment_results": {}},
        )

        with patch("aggre.enrichment.HackernewsCollector") as mock_hn_cls, \
             patch("aggre.enrichment.LobstersCollector") as mock_lob_cls:
            mock_hn = MagicMock()
            mock_hn.search_by_url.return_value = 0
            mock_hn_cls.return_value = mock_hn

            mock_lob = MagicMock()
            mock_lob.search_by_url.return_value = 0
            mock_lob_cls.return_value = mock_lob

            results = enrich_posts(engine, config, log, batch_limit=50)

        assert results == {"hackernews": 0, "lobsters": 0}
        mock_hn.search_by_url.assert_not_called()
        mock_lob.search_by_url.assert_not_called()

    def test_skips_hackernews_posts(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()

        _seed_hn_post(engine, "hn-1")

        with patch("aggre.enrichment.HackernewsCollector") as mock_hn_cls, \
             patch("aggre.enrichment.LobstersCollector") as mock_lob_cls:
            mock_hn = MagicMock()
            mock_hn.search_by_url.return_value = 0
            mock_hn_cls.return_value = mock_hn

            mock_lob = MagicMock()
            mock_lob.search_by_url.return_value = 0
            mock_lob_cls.return_value = mock_lob

            results = enrich_posts(engine, config, log, batch_limit=50)

        assert results == {"hackernews": 0, "lobsters": 0}
        mock_hn.search_by_url.assert_not_called()

    def test_respects_batch_limit(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()

        # Create 5 posts
        for i in range(5):
            _seed_rss_post(engine, f"https://example.com/{i}", f"rss-{i}")

        with patch("aggre.enrichment.HackernewsCollector") as mock_hn_cls, \
             patch("aggre.enrichment.LobstersCollector") as mock_lob_cls:
            mock_hn = MagicMock()
            mock_hn.search_by_url.return_value = 0
            mock_hn_cls.return_value = mock_hn

            mock_lob = MagicMock()
            mock_lob.search_by_url.return_value = 0
            mock_lob_cls.return_value = mock_lob

            enrich_posts(engine, config, log, batch_limit=3)

        # Should only process 3
        assert mock_hn.search_by_url.call_count == 3
        assert mock_lob.search_by_url.call_count == 3

    def test_handles_search_failure_gracefully(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()

        _seed_rss_post(engine, "https://example.com/fail", "rss-fail")

        with patch("aggre.enrichment.HackernewsCollector") as mock_hn_cls, \
             patch("aggre.enrichment.LobstersCollector") as mock_lob_cls:
            mock_hn = MagicMock()
            mock_hn.search_by_url.side_effect = Exception("HN API error")
            mock_hn_cls.return_value = mock_hn

            mock_lob = MagicMock()
            mock_lob.search_by_url.return_value = 1
            mock_lob_cls.return_value = mock_lob

            results = enrich_posts(engine, config, log, batch_limit=50)

        # HN failed but lobsters succeeded
        assert results == {"hackernews": 0, "lobsters": 1}

        # Post should still be marked as enriched
        with engine.connect() as conn:
            item = conn.execute(
                sa.select(SilverPost).where(SilverPost.external_id == "rss-fail")
            ).fetchone()
            meta = json.loads(item.meta)
            assert "enriched_at" in meta

    def test_no_pending_returns_zeros(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()

        results = enrich_posts(engine, config, log, batch_limit=50)
        assert results == {"hackernews": 0, "lobsters": 0}

    def test_skips_posts_without_url(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()

        # Create a post without a URL (shouldn't happen normally, but test the filter)
        with engine.begin() as conn:
            result = conn.execute(
                sa.insert(Source).values(type="rss", name="Test", config="{}")
            )
            source_id = result.lastrowid
            result = conn.execute(
                sa.insert(BronzePost).values(
                    source_type="rss", external_id="no-url", raw_data="{}"
                )
            )
            conn.execute(
                sa.insert(SilverPost).values(
                    source_id=source_id,
                    bronze_post_id=result.lastrowid,
                    source_type="rss",
                    external_id="no-url",
                    title="No URL Post",
                    url=None,
                )
            )

        with patch("aggre.enrichment.HackernewsCollector") as mock_hn_cls, \
             patch("aggre.enrichment.LobstersCollector") as mock_lob_cls:
            mock_hn = MagicMock()
            mock_hn_cls.return_value = mock_hn

            mock_lob = MagicMock()
            mock_lob_cls.return_value = mock_lob

            results = enrich_posts(engine, config, log, batch_limit=50)

        assert results == {"hackernews": 0, "lobsters": 0}
        mock_hn.search_by_url.assert_not_called()
