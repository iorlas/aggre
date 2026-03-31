# Run-Once Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an `aggre run-once` CLI command that runs the full pipeline once and exits, with per-source-type TTL to skip recently-fetched sources, plus a `docker-compose.local.yml` for one-command Docker execution.

**Architecture:** New `run-once` Click command in `cli.py` orchestrates existing stage functions (collect, download, extract, transcribe, enrich) sequentially, draining each stage's queue before proceeding to the next. A standalone `all_sources_recent()` helper in `collectors/base.py` checks `Source.last_fetched_at` against a TTL cutoff per source type. A separate `docker-compose.local.yml` runs postgres + migrate + run-once.

**Tech Stack:** Click (CLI), SQLAlchemy Core (TTL queries), existing collector/fetcher functions, Docker Compose.

---

### Task 1: Add TTL Helper to `collectors/base.py`

**Files:**
- Modify: `src/aggre/collectors/base.py:1-15` (imports and module-level)
- Test: `tests/test_run_once.py`

**Step 1: Write the failing test**

Create `tests/test_run_once.py`:

```python
"""Tests for run-once command and TTL helpers."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from aggre.collectors.base import all_sources_recent
from aggre.db import Source


class TestAllSourcesRecent:
    def test_no_sources_returns_false(self, engine):
        """When no sources exist for a type, should return False (need to collect to create them)."""
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is False

    def test_never_fetched_source_returns_false(self, engine):
        """A source with last_fetched_at=NULL should not be considered recent."""
        with engine.begin() as conn:
            conn.execute(sa.insert(Source).values(type="rss", name="Feed A", config="{}"))
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is False

    def test_stale_source_returns_false(self, engine):
        """A source fetched 2 hours ago with TTL=60 should not be recent."""
        stale_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with engine.begin() as conn:
            conn.execute(sa.insert(Source).values(
                type="rss", name="Feed A", config="{}", last_fetched_at=stale_time,
            ))
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is False

    def test_recent_source_returns_true(self, engine):
        """A source fetched 5 minutes ago with TTL=60 should be recent."""
        recent_time = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with engine.begin() as conn:
            conn.execute(sa.insert(Source).values(
                type="rss", name="Feed A", config="{}", last_fetched_at=recent_time,
            ))
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is True

    def test_mixed_sources_returns_false(self, engine):
        """If ANY source of the type is stale, return False."""
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with engine.begin() as conn:
            conn.execute(sa.insert(Source).values(type="rss", name="Feed A", config="{}", last_fetched_at=recent))
            conn.execute(sa.insert(Source).values(type="rss", name="Feed B", config="{}", last_fetched_at=stale))
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is False

    def test_ignores_other_source_types(self, engine):
        """TTL check should only look at the specified source type."""
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with engine.begin() as conn:
            conn.execute(sa.insert(Source).values(type="rss", name="Feed A", config="{}", last_fetched_at=recent))
            conn.execute(sa.insert(Source).values(type="reddit", name="Sub A", config="{}"))
        assert all_sources_recent(engine, "rss", ttl_minutes=60) is True
        assert all_sources_recent(engine, "reddit", ttl_minutes=60) is False
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_once.py::TestAllSourcesRecent -v`
Expected: FAIL with `ImportError: cannot import name 'all_sources_recent' from 'aggre.collectors.base'`

**Step 3: Write minimal implementation**

Add to `src/aggre/collectors/base.py` — new imports at the top and function after the existing imports:

```python
# Add to imports (line 1-14 area):
from datetime import UTC, datetime, timedelta

# Add as a module-level function BEFORE the BaseCollector class:
def all_sources_recent(engine: sa.engine.Engine, source_type: str, ttl_minutes: int) -> bool:
    """Check if ALL sources of this type were fetched within ttl_minutes.

    Returns False if no sources exist (first run — need to collect to create them).
    """
    cutoff = (datetime.now(UTC) - timedelta(minutes=ttl_minutes)).isoformat()
    with engine.connect() as conn:
        total = conn.execute(
            sa.select(sa.func.count()).select_from(Source).where(Source.type == source_type)
        ).scalar()
        if total == 0:
            return False
        stale = conn.execute(
            sa.select(sa.func.count()).select_from(Source).where(
                Source.type == source_type,
                sa.or_(Source.last_fetched_at.is_(None), Source.last_fetched_at < cutoff),
            )
        ).scalar()
        return stale == 0
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_run_once.py::TestAllSourcesRecent -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add src/aggre/collectors/base.py tests/test_run_once.py
git commit -m "feat: add all_sources_recent() TTL helper for run-once"
```

---

### Task 2: Add `run-once` CLI Command

**Files:**
- Modify: `src/aggre/cli.py` (add new command after the `status` command at end of file)
- Test: `tests/test_run_once.py` (add new test class)

**Step 1: Write the failing test**

Append to `tests/test_run_once.py`:

```python
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from aggre.cli import cli
from aggre.db import SilverContent, SilverDiscussion


class TestRunOnceCommand:
    def test_runs_all_stages_sequentially(self, engine, tmp_path: Path):
        """run-once should call collect, download, extract, transcribe, enrich in order."""
        db_url = engine.url.render_as_string(hide_password=False)
        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text("rss:\n  - name: Test\n    url: https://example.com/feed.xml\n")

        call_order = []

        mock_rss = MagicMock()
        mock_rss.source_type = "rss"
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
            return {"hackernews": 0, "lobsters": 0}

        runner = CliRunner()
        env = {"AGGRE_DATABASE_URL": db_url, "AGGRE_LOG_DIR": str(tmp_path / "logs")}
        with patch.dict(os.environ, env), \
             patch("aggre.cli.RssCollector", return_value=mock_rss), \
             patch("aggre.cli.RedditCollector", return_value=MagicMock(source_type="reddit", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.YoutubeCollector", return_value=MagicMock(source_type="youtube", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.HackernewsCollector", return_value=MagicMock(source_type="hackernews", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.LobstersCollector", return_value=MagicMock(source_type="lobsters", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.HuggingfaceCollector", return_value=MagicMock(source_type="huggingface", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.TelegramCollector", return_value=MagicMock(source_type="telegram", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.download_content", side_effect=mock_download), \
             patch("aggre.cli.extract_html_text", side_effect=mock_extract), \
             patch("aggre.cli.do_transcribe", side_effect=mock_transcribe), \
             patch("aggre.cli.enrich_content_discussions", side_effect=mock_enrich):
            result = runner.invoke(cli, ["--config", config_path, "run-once"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        assert call_order == ["download", "extract", "transcribe", "enrich"]
        assert "Run Complete" in result.output

    def test_skips_recent_sources(self, engine, tmp_path: Path):
        """run-once --source-ttl should skip sources fetched within the TTL."""
        db_url = engine.url.render_as_string(hide_password=False)

        # Seed a recently-fetched source
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with engine.begin() as conn:
            conn.execute(sa.insert(Source).values(
                type="rss", name="Test", config="{}", last_fetched_at=recent,
            ))

        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text("rss:\n  - name: Test\n    url: https://example.com/feed.xml\n")

        mock_rss = MagicMock()
        mock_rss.source_type = "rss"
        mock_rss.collect.return_value = 0

        runner = CliRunner()
        env = {"AGGRE_DATABASE_URL": db_url, "AGGRE_LOG_DIR": str(tmp_path / "logs")}
        with patch.dict(os.environ, env), \
             patch("aggre.cli.RssCollector", return_value=mock_rss), \
             patch("aggre.cli.RedditCollector", return_value=MagicMock(source_type="reddit", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.YoutubeCollector", return_value=MagicMock(source_type="youtube", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.HackernewsCollector", return_value=MagicMock(source_type="hackernews", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.LobstersCollector", return_value=MagicMock(source_type="lobsters", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.HuggingfaceCollector", return_value=MagicMock(source_type="huggingface", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.TelegramCollector", return_value=MagicMock(source_type="telegram", collect=MagicMock(return_value=0))), \
             patch("aggre.cli.download_content", return_value=0), \
             patch("aggre.cli.extract_html_text", return_value=0), \
             patch("aggre.cli.do_transcribe", return_value=0), \
             patch("aggre.cli.enrich_content_discussions", return_value={"hackernews": 0, "lobsters": 0}):
            result = runner.invoke(cli, ["--config", config_path, "run-once", "--source-ttl", "60"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        mock_rss.collect.assert_not_called()
        assert "skipped" in result.output.lower()

    def test_skip_transcribe_flag(self, engine, tmp_path: Path):
        """run-once --skip-transcribe should skip the transcription stage."""
        db_url = engine.url.render_as_string(hide_password=False)
        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text("")

        runner = CliRunner()
        env = {"AGGRE_DATABASE_URL": db_url, "AGGRE_LOG_DIR": str(tmp_path / "logs")}
        with patch.dict(os.environ, env), \
             patch("aggre.cli.download_content", return_value=0), \
             patch("aggre.cli.extract_html_text", return_value=0), \
             patch("aggre.cli.do_transcribe", return_value=0) as mock_transcribe, \
             patch("aggre.cli.enrich_content_discussions", return_value={"hackernews": 0, "lobsters": 0}):
            result = runner.invoke(cli, ["--config", config_path, "run-once", "--skip-transcribe"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        mock_transcribe.assert_not_called()

    def test_drain_loop_processes_multiple_batches(self, engine, tmp_path: Path):
        """Downstream stages should loop until they return 0 (drain the queue)."""
        db_url = engine.url.render_as_string(hide_password=False)
        config_path = str(tmp_path / "config.yaml")
        Path(config_path).write_text("")

        # download_content returns 50, 50, 0 (two full batches then empty)
        download_calls = iter([50, 50, 0])

        runner = CliRunner()
        env = {"AGGRE_DATABASE_URL": db_url, "AGGRE_LOG_DIR": str(tmp_path / "logs")}
        with patch.dict(os.environ, env), \
             patch("aggre.cli.download_content", side_effect=lambda *a, **kw: next(download_calls)), \
             patch("aggre.cli.extract_html_text", return_value=0), \
             patch("aggre.cli.do_transcribe", return_value=0), \
             patch("aggre.cli.enrich_content_discussions", return_value={"hackernews": 0, "lobsters": 0}):
            result = runner.invoke(cli, ["--config", config_path, "run-once", "--skip-transcribe"])

        assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
        # Summary should show 100 downloaded (50 + 50)
        assert "100" in result.output
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_once.py::TestRunOnceCommand -v`
Expected: FAIL — the `run-once` command doesn't exist yet.

**Step 3: Write the implementation**

Add to `src/aggre/cli.py`, after all existing commands (after line 322). The command uses top-level imports for the stage functions so they can be patched in tests:

```python
# At the TOP of cli.py, add these imports (alongside existing ones):
from aggre.collectors.base import all_sources_recent
from aggre.collectors.hackernews import HackernewsCollector
from aggre.collectors.huggingface import HuggingfaceCollector
from aggre.collectors.lobsters import LobstersCollector
from aggre.collectors.reddit import RedditCollector
from aggre.collectors.rss import RssCollector
from aggre.collectors.telegram import TelegramCollector
from aggre.collectors.youtube import YoutubeCollector
from aggre.content_fetcher import download_content, extract_html_text
from aggre.enrichment import enrich_content_discussions
from aggre.transcriber import transcribe as do_transcribe

# At the END of cli.py, add the run-once command:

_MAX_DRAIN_ITERATIONS = 100  # Safety limit to prevent infinite loops


@cli.command("run-once")
@click.option(
    "--source-ttl", default=0, type=int,
    help="Skip sources fetched within this many minutes (0 = always collect).",
)
@click.option(
    "--source", "source_type",
    type=click.Choice(["rss", "reddit", "youtube", "hackernews", "lobsters", "huggingface", "telegram"]),
    help="Collect only this source type.",
)
@click.option("--skip-transcribe", is_flag=True, help="Skip the transcription stage.")
@click.option("--comment-batch", default=10, type=int, help="Max comments to fetch per source per cycle (0 = skip).")
@click.pass_context
def run_once_cmd(
    ctx: click.Context,
    source_ttl: int,
    source_type: str | None,
    skip_transcribe: bool,
    comment_batch: int,
) -> None:
    """Run the full pipeline once and exit."""
    cfg = ctx.obj["config"]
    engine = ctx.obj["engine"]
    log = setup_logging(cfg.settings.log_dir, "run-once")

    collectors = {
        "rss": RssCollector(),
        "reddit": RedditCollector(),
        "youtube": YoutubeCollector(),
        "hackernews": HackernewsCollector(),
        "lobsters": LobstersCollector(),
        "huggingface": HuggingfaceCollector(),
        "telegram": TelegramCollector(),
    }

    active_collectors = collectors
    if source_type:
        active_collectors = {source_type: collectors[source_type]}

    # --- Stage 1: Collect ---
    sources_collected = 0
    sources_skipped = 0
    total_new_discussions = 0

    for name, collector in active_collectors.items():
        if source_ttl > 0 and all_sources_recent(engine, collector.source_type, source_ttl):
            log.info("run_once.source_skipped", source=name, reason="recent")
            sources_skipped += 1
            continue
        try:
            count = collector.collect(engine, cfg, log)
            total_new_discussions += count
            sources_collected += 1
            log.info("run_once.source_collected", source=name, new_discussions=count)
        except Exception:
            log.exception("run_once.source_error", source=name)
            sources_collected += 1

    # Fetch comments for sources that support it
    for src_name in ("reddit", "hackernews", "lobsters"):
        if source_type in (None, src_name) and comment_batch > 0:
            coll = active_collectors.get(src_name) or collectors.get(src_name)
            if coll and hasattr(coll, "collect_comments"):
                try:
                    coll.collect_comments(engine, cfg, log, batch_limit=comment_batch)
                except Exception:
                    log.exception("run_once.comments_error", source=src_name)

    # --- Stage 2: Download ---
    total_downloaded = 0
    for _ in range(_MAX_DRAIN_ITERATIONS):
        batch = download_content(engine, cfg, log)
        total_downloaded += batch
        if batch == 0:
            break

    # --- Stage 3: Extract text ---
    total_extracted = 0
    for _ in range(_MAX_DRAIN_ITERATIONS):
        batch = extract_html_text(engine, cfg, log)
        total_extracted += batch
        if batch == 0:
            break

    # --- Stage 4: Transcribe ---
    total_transcribed = 0
    if not skip_transcribe:
        for _ in range(_MAX_DRAIN_ITERATIONS):
            batch = do_transcribe(engine, cfg, log)
            total_transcribed += batch
            if batch == 0:
                break

    # --- Stage 5: Enrich ---
    total_enriched = 0
    for _ in range(_MAX_DRAIN_ITERATIONS):
        result = enrich_content_discussions(engine, cfg, log)
        batch_total = sum(result.values())
        total_enriched += batch_total
        if batch_total == 0:
            break

    # --- Summary ---
    click.echo("\n=== Run Complete ===")
    click.echo(f"Sources:  {sources_collected + sources_skipped} checked, {sources_collected} collected, {sources_skipped} skipped (recent)")
    click.echo(f"Discuss:  {total_new_discussions} new")
    click.echo(f"Content:  {total_downloaded} downloaded")
    click.echo(f"Extract:  {total_extracted} extracted")
    if skip_transcribe:
        click.echo("Transcr:  skipped")
    else:
        click.echo(f"Transcr:  {total_transcribed} transcribed")
    click.echo(f"Enrich:   {total_enriched} enriched")
```

**Important implementation note:** The existing `collect_cmd` uses lazy imports inside the function body. The `run-once` command uses top-level imports instead so they can be patched in tests via `patch("aggre.cli.RssCollector", ...)`. This means these imports will run when `cli.py` is first loaded. If any collector has heavy import-time dependencies, move them back to lazy imports and adjust test patches to target the module path (e.g., `patch("aggre.collectors.rss.RssCollector", ...)`).

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_run_once.py -v`
Expected: All tests PASS (both TestAllSourcesRecent and TestRunOnceCommand)

**Step 5: Run full test suite to check nothing broke**

Run: `pytest tests/ -v`
Expected: All existing tests still PASS

**Step 6: Commit**

```bash
git add src/aggre/cli.py tests/test_run_once.py
git commit -m "feat: add aggre run-once command for one-shot pipeline execution"
```

---

### Task 3: Create `docker-compose.local.yml`

**Files:**
- Create: `docker-compose.local.yml`

**Step 1: Create the file**

```yaml
# One-shot local execution: start DB, migrate, run full pipeline, exit.
# Usage: docker compose -f docker-compose.local.yml up --build
#
# Data persists in ./data/postgres/ between runs. Re-running skips
# recently-fetched sources (--source-ttl) and already-processed content.

x-app-env: &app-env
  AGGRE_DATABASE_URL: postgresql+psycopg2://aggre:aggre@postgres/aggre

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: aggre
      POSTGRES_USER: aggre
      POSTGRES_PASSWORD: aggre
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aggre"]
      interval: 5s
      timeout: 5s
      retries: 5

  migrate:
    build: .
    command: uv run alembic upgrade head
    volumes:
      - ./data/app:/app/data
      - ./config.yaml:/app/config.yaml:ro
    environment:
      <<: *app-env
    env_file:
      - path: .env
        required: false
    depends_on:
      postgres:
        condition: service_healthy

  run-once:
    build: .
    command: uv run aggre run-once --source-ttl 60 --skip-transcribe
    volumes:
      - ./data/app:/app/data
      - ./config.yaml:/app/config.yaml:ro
    environment:
      <<: *app-env
    env_file:
      - path: .env
        required: false
    depends_on:
      migrate:
        condition: service_completed_successfully
```

**Step 2: Validate compose file syntax**

Run: `docker compose -f docker-compose.local.yml config --quiet`
Expected: No errors (exit code 0). If Docker is not installed locally, skip this step.

**Step 3: Commit**

```bash
git add docker-compose.local.yml
git commit -m "feat: add docker-compose.local.yml for one-shot local execution"
```

---

### Task 4: Final Verification

**Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass, including new `test_run_once.py` tests.

**Step 2: Verify CLI help**

Run: `uv run aggre run-once --help`
Expected output shows:
```
Usage: aggre run-once [OPTIONS]

  Run the full pipeline once and exit.

Options:
  --source-ttl INTEGER            Skip sources fetched within this many minutes (0 = always collect).
  --source [rss|reddit|youtube|hackernews|lobsters|huggingface|telegram]
                                  Collect only this source type.
  --skip-transcribe               Skip the transcription stage.
  --comment-batch INTEGER         Max comments to fetch per source per cycle (0 = skip).
  --help                          Show this message and exit.
```

**Step 3: Commit any fixups if needed**

```bash
git add -A
git commit -m "fix: address test/lint issues from run-once implementation"
```
