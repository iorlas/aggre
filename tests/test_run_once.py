"""Tests for the run-once TTL helper and run-once CLI command."""

from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from click.testing import CliRunner

from aggre.cli import cli
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


# ---------------------------------------------------------------------------
# TestRunOnceCommand — tests for the `aggre run-once` CLI command
# ---------------------------------------------------------------------------


def _collector_patches(mocks: dict[str, MagicMock] | None = None) -> list:
    """Return a list of patch context managers for all collector constructors."""
    modules = {
        "rss": "aggre.collectors.rss.RssCollector",
        "reddit": "aggre.collectors.reddit.RedditCollector",
        "youtube": "aggre.collectors.youtube.YoutubeCollector",
        "hackernews": "aggre.collectors.hackernews.HackernewsCollector",
        "lobsters": "aggre.collectors.lobsters.LobstersCollector",
        "huggingface": "aggre.collectors.huggingface.HuggingfaceCollector",
        "telegram": "aggre.collectors.telegram.TelegramCollector",
    }
    mocks = mocks or {}
    patches = []
    for name, module_path in modules.items():
        mock = mocks.get(name, MagicMock())
        mock.source_type = name
        mock.collect.return_value = mock.collect.return_value if name in mocks else 0
        patches.append(patch(module_path, return_value=mock))
    return patches


def _make_env(engine, tmp_path: Path) -> tuple[str, dict[str, str]]:
    """Return (config_path, env_dict) for CliRunner invocations."""
    db_url = engine.url.render_as_string(hide_password=False)
    config_path = str(tmp_path / "config.yaml")
    Path(config_path).write_text(
        "rss:\n  - name: Test\n    url: https://example.com/feed.xml\n"
    )
    env = {"AGGRE_DATABASE_URL": db_url, "AGGRE_LOG_DIR": str(tmp_path / "logs")}
    return config_path, env


class TestRunOnceCommand:
    """Tests for the run-once CLI command."""

    def test_runs_all_stages_sequentially(self, engine, tmp_path: Path):
        """run-once should execute collect, download, extract, transcribe, enrich in order."""
        config_path, env = _make_env(engine, tmp_path)

        call_order = []

        mock_rss = MagicMock()
        mock_rss.collect.return_value = 5

        def mock_download(*a, **kw):
            call_order.append("download")
            return 0

        def mock_extract(*a, **kw):
            call_order.append("extract")
            return 0

        def mock_transcribe(*a, **kw):
            call_order.append("transcribe")
            return 0

        def mock_enrich(*a, **kw):
            call_order.append("enrich")
            return {}

        runner = CliRunner()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            for p in _collector_patches({"rss": mock_rss}):
                stack.enter_context(p)
            stack.enter_context(patch("aggre.collectors.base.all_sources_recent", return_value=False))
            stack.enter_context(patch("aggre.content_fetcher.download_content", side_effect=mock_download))
            stack.enter_context(patch("aggre.content_fetcher.extract_html_text", side_effect=mock_extract))
            stack.enter_context(patch("aggre.transcriber.transcribe", side_effect=mock_transcribe))
            stack.enter_context(patch("aggre.enrichment.enrich_content_discussions", side_effect=mock_enrich))

            result = runner.invoke(cli, ["--config", config_path, "run-once"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert "Run Complete" in result.output

        # Verify ordering: download before extract before transcribe before enrich
        assert call_order == ["download", "extract", "transcribe", "enrich"]

    def test_skips_recent_sources(self, engine, tmp_path: Path):
        """run-once --source-ttl 60 should skip sources fetched recently."""
        config_path, env = _make_env(engine, tmp_path)

        # Seed a recently-fetched RSS source (5 min ago)
        five_min_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="rss", name="Test", config="{}", last_fetched_at=five_min_ago
                )
            )

        mock_rss = MagicMock()
        mock_rss.collect.return_value = 0

        runner = CliRunner()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            for p in _collector_patches({"rss": mock_rss}):
                stack.enter_context(p)
            stack.enter_context(patch("aggre.content_fetcher.download_content", return_value=0))
            stack.enter_context(patch("aggre.content_fetcher.extract_html_text", return_value=0))
            stack.enter_context(patch("aggre.transcriber.transcribe", return_value=0))
            stack.enter_context(patch("aggre.enrichment.enrich_content_discussions", return_value={}))

            result = runner.invoke(cli, [
                "--config", config_path, "run-once",
                "--source-ttl", "60", "--source", "rss",
            ])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        mock_rss.collect.assert_not_called()
        assert "skipped" in result.output.lower()

    def test_skip_transcribe_flag(self, engine, tmp_path: Path):
        """run-once --skip-transcribe should not call transcribe()."""
        config_path, env = _make_env(engine, tmp_path)

        runner = CliRunner()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            for p in _collector_patches():
                stack.enter_context(p)
            stack.enter_context(patch("aggre.collectors.base.all_sources_recent", return_value=False))
            stack.enter_context(patch("aggre.content_fetcher.download_content", return_value=0))
            stack.enter_context(patch("aggre.content_fetcher.extract_html_text", return_value=0))
            mock_tr = stack.enter_context(patch("aggre.transcriber.transcribe", return_value=0))
            stack.enter_context(patch("aggre.enrichment.enrich_content_discussions", return_value={}))

            result = runner.invoke(cli, [
                "--config", config_path, "run-once", "--skip-transcribe",
            ])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        mock_tr.assert_not_called()
        assert "skipped" in result.output.lower()

    def test_drain_loop_processes_multiple_batches(self, engine, tmp_path: Path):
        """Drain loop should call download_content repeatedly until it returns 0."""
        config_path, env = _make_env(engine, tmp_path)

        download_returns = iter([50, 50, 0])

        runner = CliRunner()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            for p in _collector_patches():
                stack.enter_context(p)
            stack.enter_context(patch("aggre.collectors.base.all_sources_recent", return_value=False))
            stack.enter_context(patch("aggre.content_fetcher.download_content", side_effect=lambda *a, **kw: next(download_returns)))
            stack.enter_context(patch("aggre.content_fetcher.extract_html_text", return_value=0))
            stack.enter_context(patch("aggre.enrichment.enrich_content_discussions", return_value={}))

            result = runner.invoke(cli, [
                "--config", config_path, "run-once", "--skip-transcribe",
            ])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert "100" in result.output
