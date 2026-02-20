"""Tests for the run-once TTL helper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from aggre.collectors.base import all_sources_recent
from aggre.db import Source


class TestAllSourcesRecent:
    """Tests for all_sources_recent() TTL check."""

    def test_no_sources_returns_false(self, engine):
        """No sources in DB -> False (first run, need to create them)."""
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is False

    def test_never_fetched_source_returns_false(self, engine):
        """Source with NULL last_fetched_at -> False."""
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="rss", name="feed-a", config="{}", last_fetched_at=None
                )
            )
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is False

    def test_stale_source_returns_false(self, engine):
        """Source fetched 2 hours ago, TTL=60 -> False."""
        two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="rss", name="feed-a", config="{}", last_fetched_at=two_hours_ago
                )
            )
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is False

    def test_recent_source_returns_true(self, engine):
        """Source fetched 5 min ago, TTL=60 -> True."""
        five_min_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="rss", name="feed-a", config="{}", last_fetched_at=five_min_ago
                )
            )
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is True

    def test_mixed_sources_returns_false(self, engine):
        """One recent + one stale -> False."""
        five_min_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="rss", name="feed-a", config="{}", last_fetched_at=five_min_ago
                )
            )
            conn.execute(
                sa.insert(Source).values(
                    type="rss", name="feed-b", config="{}", last_fetched_at=two_hours_ago
                )
            )
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is False

    def test_ignores_other_source_types(self, engine):
        """rss recent + reddit never-fetched -> rss True, reddit False."""
        five_min_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="rss", name="feed-a", config="{}", last_fetched_at=five_min_ago
                )
            )
            conn.execute(
                sa.insert(Source).values(
                    type="reddit", name="sub-a", config="{}", last_fetched_at=None
                )
            )
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is True
        assert all_sources_recent(engine, "reddit", ttl_minutes=60) is False
