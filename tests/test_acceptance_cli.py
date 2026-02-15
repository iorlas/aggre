"""Acceptance tests for CLI commands and Alembic migrations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from click.testing import CliRunner

from aggre.cli import cli
from aggre.db import Base, SilverContent, SilverDiscussion, Source


# ---------------------------------------------------------------------------
# Part 1: Migration tests
# ---------------------------------------------------------------------------


def _run_alembic(database_url: str, target: str):
    """Run an alembic migration against a PostgreSQL database."""
    from alembic import command
    from alembic.config import Config

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
        assert "bronze_discussions" in table_names  # renamed from bronze_posts by migration 002
        assert "silver_content" in table_names
        assert "silver_discussions" in table_names
        assert "sources" in table_names

        # Tables that should NOT exist (dropped or renamed)
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


# ---------------------------------------------------------------------------
# Part 2: CLI status command
# ---------------------------------------------------------------------------


class TestCliStatus:
    def test_status_output_sections(self, engine, tmp_path: Path):
        """The status command should print Sources, Discussions by Source, Content Status, Transcription Queue."""
        db_url = engine.url.render_as_string(hide_password=False)

        # Seed some data
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="rss", name="Test Feed", config="{}", last_fetched_at="2026-01-01T00:00:00Z"
                )
            )
            conn.execute(
                sa.insert(SilverContent).values(
                    canonical_url="https://example.com/article",
                    domain="example.com",
                    fetch_status="fetched",
                    transcription_status="pending",
                )
            )
            source_id = conn.execute(sa.select(Source.id)).scalar()
            content_id = conn.execute(sa.select(SilverContent.id)).scalar()
            conn.execute(
                sa.insert(SilverDiscussion).values(
                    source_id=source_id,
                    content_id=content_id,
                    source_type="rss",
                    external_id="post-1",
                    title="Test Post",
                    url="https://example.com/article",
                )
            )

        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text(
            f"settings:\n  database_url: \"{db_url}\"\n  log_dir: {tmp_path / 'logs'}\n"
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["--config", config_path, "status"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert "Sources" in result.output
        assert "Discussions by Source" in result.output
        assert "Content Status" in result.output
        assert "Transcription Queue" in result.output
        assert "Test Feed" in result.output

    def test_status_empty_db(self, engine, tmp_path: Path):
        """Status should work on an empty DB without errors."""
        db_url = engine.url.render_as_string(hide_password=False)

        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text(
            f"settings:\n  database_url: \"{db_url}\"\n  log_dir: {tmp_path / 'logs'}\n"
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["--config", config_path, "status"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert "Sources" in result.output
        assert "No sources registered yet. Run 'aggre collect' first." in result.output


# ---------------------------------------------------------------------------
# Part 3: CLI backfill-content
# ---------------------------------------------------------------------------


class TestCliBackfillContent:
    def test_backfill_links_discussions_to_content(self, engine, tmp_path: Path):
        """backfill-content should create SilverContent rows and link discussions."""
        db_url = engine.url.render_as_string(hide_password=False)

        # Seed discussions with URLs but no content_id, with meta containing score/comments
        with engine.begin() as conn:
            conn.execute(
                sa.insert(SilverDiscussion).values(
                    source_type="rss",
                    external_id="d1",
                    title="Discussion 1",
                    url="https://example.com/article-1",
                    meta=json.dumps({"score": 42, "num_comments": 10}),
                )
            )
            conn.execute(
                sa.insert(SilverDiscussion).values(
                    source_type="hackernews",
                    external_id="d2",
                    title="Discussion 2",
                    url="https://example.com/article-2",
                    meta=json.dumps({"points": 100, "comment_count": 25}),
                )
            )

        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text(
            f"settings:\n  database_url: \"{db_url}\"\n  log_dir: {tmp_path / 'logs'}\n"
        )

        runner = CliRunner()
        result = runner.invoke(cli, ["--config", config_path, "backfill-content"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert "Linked 2 discussions" in result.output

        # Verify results
        with engine.connect() as conn:
            # All discussions should have content_id
            discussions = conn.execute(
                sa.select(
                    SilverDiscussion.external_id,
                    SilverDiscussion.content_id,
                    SilverDiscussion.score,
                    SilverDiscussion.comment_count,
                )
                .order_by(SilverDiscussion.external_id)
            ).fetchall()

            assert len(discussions) == 2

            d1 = discussions[0]
            assert d1.content_id is not None
            assert d1.score == 42
            assert d1.comment_count == 10

            d2 = discussions[1]
            assert d2.content_id is not None
            assert d2.score == 100
            assert d2.comment_count == 25

            # SilverContent rows were created
            content_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(content_rows) == 2


# ---------------------------------------------------------------------------
# Part 4: CLI collect (no longer calls content fetcher or enrichment)
# ---------------------------------------------------------------------------


class TestCliCollect:
    def test_collect_calls_collector_only(self, engine, tmp_path: Path):
        """collect --source rss should call the RSS collector but NOT content fetcher or enrichment."""
        db_url = engine.url.render_as_string(hide_password=False)

        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text(
            f"settings:\n  database_url: \"{db_url}\"\n  log_dir: {tmp_path / 'logs'}\n"
            f"rss:\n  - name: Test\n    url: https://example.com/feed.xml\n"
        )

        mock_rss_collector = MagicMock()
        mock_rss_collector.collect.return_value = 3

        runner = CliRunner()
        with patch("aggre.collectors.rss.RssCollector", return_value=mock_rss_collector), \
             patch("aggre.collectors.reddit.RedditCollector", return_value=MagicMock()), \
             patch("aggre.collectors.youtube.YoutubeCollector", return_value=MagicMock()), \
             patch("aggre.collectors.hackernews.HackernewsCollector", return_value=MagicMock()), \
             patch("aggre.collectors.lobsters.LobstersCollector", return_value=MagicMock()), \
             patch("aggre.collectors.huggingface.HuggingfaceCollector", return_value=MagicMock()), \
             patch("aggre.content_fetcher.download_content", return_value=0) as mock_download, \
             patch("aggre.enrichment.enrich_content_discussions", return_value={}) as mock_enrich:
            result = runner.invoke(cli, ["--config", config_path, "collect", "--source", "rss"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        mock_rss_collector.collect.assert_called_once()
        mock_download.assert_not_called()
        mock_enrich.assert_not_called()


# ---------------------------------------------------------------------------
# Part 5: CLI download command
# ---------------------------------------------------------------------------


class TestCliDownload:
    def test_download_invokes_download_content(self, engine, tmp_path: Path):
        """download command should invoke download_content."""
        db_url = engine.url.render_as_string(hide_password=False)

        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text(
            f"settings:\n  database_url: \"{db_url}\"\n  log_dir: {tmp_path / 'logs'}\n"
        )

        runner = CliRunner()
        with patch("aggre.content_fetcher.download_content", return_value=5) as mock_download:
            result = runner.invoke(cli, ["--config", config_path, "download", "--batch", "10", "--workers", "3"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        mock_download.assert_called_once()
        call_kwargs = mock_download.call_args
        assert call_kwargs[1]["batch_limit"] == 10
        assert call_kwargs[1]["max_workers"] == 3


# ---------------------------------------------------------------------------
# Part 6: CLI extract-html-text command
# ---------------------------------------------------------------------------


class TestCliExtractHtmlText:
    def test_extract_invokes_extract_html_text(self, engine, tmp_path: Path):
        """extract-html-text command should invoke extract_html_text."""
        db_url = engine.url.render_as_string(hide_password=False)

        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text(
            f"settings:\n  database_url: \"{db_url}\"\n  log_dir: {tmp_path / 'logs'}\n"
        )

        runner = CliRunner()
        with patch("aggre.content_fetcher.extract_html_text", return_value=3) as mock_extract:
            result = runner.invoke(cli, ["--config", config_path, "extract-html-text", "--batch", "25"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        mock_extract.assert_called_once()
        assert mock_extract.call_args[1]["batch_limit"] == 25


# ---------------------------------------------------------------------------
# Part 7: CLI enrich-content-discussions command
# ---------------------------------------------------------------------------


class TestCliEnrich:
    def test_enrich_invokes_enrich_content_discussions(self, engine, tmp_path: Path):
        """enrich-content-discussions command should invoke enrich_content_discussions."""
        db_url = engine.url.render_as_string(hide_password=False)

        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text(
            f"settings:\n  database_url: \"{db_url}\"\n  log_dir: {tmp_path / 'logs'}\n"
        )

        runner = CliRunner()
        with patch("aggre.enrichment.enrich_content_discussions", return_value={"hackernews": 2, "lobsters": 1}) as mock_enrich:
            result = runner.invoke(cli, ["--config", config_path, "enrich-content-discussions", "--batch", "30"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        mock_enrich.assert_called_once()
        assert mock_enrich.call_args[1]["batch_limit"] == 30
