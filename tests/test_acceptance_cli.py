"""Acceptance tests for Alembic migrations."""

from __future__ import annotations

import os
from unittest.mock import patch

import sqlalchemy as sa

from aggre.db import Base

# ---------------------------------------------------------------------------
# Part 1: Migration tests
# ---------------------------------------------------------------------------


def _run_alembic(database_url: str, target: str):
    """Run an alembic migration against a PostgreSQL database."""
    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)

    with patch.dict(os.environ, {"AGGRE_DATABASE_URL": database_url}):
        if target.startswith("-") or target == "base":
            command.downgrade(alembic_cfg, target)
        else:
            command.upgrade(alembic_cfg, target)


class TestAlembicMigration:
    def _get_db_url(self):
        return os.environ.get("AGGRE_TEST_DATABASE_URL", "postgresql+psycopg2://aggre:aggre@localhost/aggre_test")

    def _clean_db(self, database_url: str):
        """Drop all tables so we can test alembic from scratch."""
        engine = sa.create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(sa.text("DROP SCHEMA public CASCADE"))
            conn.execute(sa.text("CREATE SCHEMA public"))
        engine.dispose()

    def test_upgrade_head_creates_expected_tables(self):
        """Run alembic upgrade head on a fresh DB and verify schema."""
        db_url = self._get_db_url()
        self._clean_db(db_url)

        _run_alembic(db_url, "head")

        engine = sa.create_engine(db_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())

        # Tables that SHOULD exist after upgrade head
        assert "silver_content" in table_names
        assert "silver_discussions" in table_names
        assert "sources" in table_names

        # Tables that should NOT exist (removed)
        assert "bronze_discussions" not in table_names
        assert "silver_comments" not in table_names
        assert "bronze_comments" not in table_names
        assert "silver_posts" not in table_names

        # Verify indexes on silver_discussions
        sd_indexes = {idx["name"] for idx in inspector.get_indexes("silver_discussions")}
        assert "idx_silver_discussions_source_type" in sd_indexes
        assert "idx_silver_discussions_published" in sd_indexes
        assert "idx_silver_discussions_source_id" in sd_indexes
        assert "idx_silver_discussions_external" in sd_indexes
        assert "idx_silver_discussions_comments_status" in sd_indexes
        assert "idx_silver_discussions_url" in sd_indexes
        assert "idx_silver_discussions_content_id" in sd_indexes

        # Verify indexes on silver_content
        sc_indexes = {idx["name"] for idx in inspector.get_indexes("silver_content")}
        assert "idx_silver_content_domain" in sc_indexes
        assert "idx_silver_content_fetch_status" in sc_indexes
        assert "idx_silver_content_transcription" in sc_indexes

        engine.dispose()

        # Restore tables for subsequent tests (conftest expects tables to exist)
        self._clean_db(db_url)
        engine = sa.create_engine(db_url)
        Base.metadata.create_all(engine)
        engine.dispose()

    def test_downgrade_removes_tables(self):
        """After downgrade from head, tables should be gone."""
        db_url = self._get_db_url()
        self._clean_db(db_url)

        _run_alembic(db_url, "head")
        _run_alembic(db_url, "base")

        engine = sa.create_engine(db_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())

        assert "silver_discussions" not in table_names
        assert "silver_content" not in table_names
        assert "bronze_discussions" not in table_names
        assert "sources" not in table_names

        engine.dispose()

        # Restore tables for subsequent tests
        self._clean_db(db_url)
        engine = sa.create_engine(db_url)
        Base.metadata.create_all(engine)
        engine.dispose()
