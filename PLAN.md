# Compliance Validation Plan: medallion-guidelines.md & python-guidelines.md

## Scope

Validate and fix compliance violations against `docs/medallion-guidelines.md` and `docs/python-guidelines.md`.

## Violations Found

### Python Guidelines

1. **Missing `from __future__ import annotations`** (8 files)
   - `src/aggre/__init__.py`
   - `src/aggre/collectors/hackernews/__init__.py`
   - `src/aggre/collectors/huggingface/__init__.py`
   - `src/aggre/collectors/lobsters/__init__.py`
   - `src/aggre/collectors/reddit/__init__.py`
   - `src/aggre/collectors/rss/__init__.py`
   - `src/aggre/collectors/telegram/__init__.py`
   - `src/aggre/collectors/youtube/__init__.py`
   - Note: Dagster files (sensors.py, job files) intentionally omit this with documented reason — Dagster decorators inspect type hints at decoration time. Not a violation.

2. **Unparameterized `dict` (implicit `Any`)** (6 locations)
   - `collectors/hackernews/collector.py:165` — `hit: dict` → `hit: dict[str, object]`
   - `collectors/reddit/collector.py:185` — `post_data: dict` → `dict[str, object]`
   - `collectors/lobsters/collector.py:198` — `story: dict` → `dict[str, object]`
   - `collectors/huggingface/collector.py:72` — `item: dict` → `dict[str, object]`
   - `collectors/reddit/collector.py:58` — return `tuple[dict, ...]` → `tuple[dict[str, object], ...]`
   - `collectors/lobsters/collector.py:31` — `dict[str, list[dict]]` → `dict[str, list[dict[str, object]]]`

3. **Untyped parameters** (2 locations)
   - `collectors/telegram/collector.py:69` — `_collect_channel(self, client, engine, source_id, tg_source, config, settings, log)` — all params untyped
   - `collectors/reddit/collector.py:29` — `_should_retry(retry_state)` — param untyped

### Medallion Guidelines

4. **Content fetcher missing bronze pre-call check**
   - `content_fetcher.py:79` — `_download_one()` calls `client.get(url)` without checking if HTML already exists in bronze via `bronze_exists_by_url()`. Violates read-through cache pattern.

### Documentation Drift

5. **`docs/semantic-model.md` references removed schema elements**
   - `bronze_discussions` table (lines 21-33) — removed per DECISIONS.md
   - `raw_html` column in `silver_content` (line 45) — removed per DECISIONS.md
   - `bronze_discussion_id` FK in `silver_discussions` (line 68) — removed
   - Relationship reference (line 99) — stale

## Execution Steps

### Step 1: Add `from __future__ import annotations` to __init__.py files
Add the import to all 8 files missing it.
**Success**: `ruff check src/` passes.

### Step 2: Fix unparameterized `dict` types
Change bare `dict` to `dict[str, object]` in collector _store_discussion methods and related signatures.
**Success**: `ty check` passes. No implicit `Any` via bare `dict`.

### Step 3: Fix untyped parameters
Add type annotations to `_collect_channel` and `_should_retry`.
**Success**: `ruff check src/` and `ty check` pass.

### Step 4: Add bronze pre-call check to content_fetcher._download_one()
Before calling `client.get(url)`, check `bronze_exists_by_url("content", url, "response", "html")`. If hit, skip HTTP call, go straight to marking as DOWNLOADED.
**Success**: Tests pass. Content already in bronze is not re-downloaded.

### Step 5: Update semantic-model.md
Remove `bronze_discussions` table, `raw_html` column, `bronze_discussion_id` FK, and stale relationship reference.
**Success**: Documentation matches actual schema in `db.py`.

### Step 6: Final verification
Run `make test`, `ruff check src/ tests/`, `ruff format --check src/ tests/`, `ty check`.
**Success**: All pass with zero violations.
