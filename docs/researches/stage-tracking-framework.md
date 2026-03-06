# Stage Tracking: From Passive Toolkit to Active Framework

**Date:** 2026-03-05
**Status:** Analysis complete, fixes applied, framework design pending

## Problem Statement

The `StageTracking` table and its helpers (`upsert_done`, `upsert_failed`, `upsert_skipped`, `retry_filter`) form a **passive toolkit**: each stage job is responsible for calling the right helper at the right time. The system relies on developers remembering to wrap every code path in try/except and call the appropriate tracking function.

This violates a **completeness invariant**: after a stage processes an item, there must always be exactly one of {done, failed, skipped} recorded. When code outside the try/except block raises, the exception propagates to Dagster, which retries the entire batch — but no tracking row is written, so the failing item is retried forever without any error record.

### The Pattern

Every stage had the same structural bug:

```python
def process_one(item):
    # Pre-checks OUTSIDE try/except — can raise!
    data = json.loads(item.meta)          # malformed JSON
    cached = read_bronze_or_none(...)     # S3 unreachable
    if cached:
        upsert_done(...)                  # DB error
        return

    try:
        # Main processing — properly guarded
        ...
        upsert_done(...)
    except Exception:
        upsert_failed(...)
```

The `json.loads`, S3 cache check, and `upsert_done` calls before the try block are all potential crash points that escape tracking.

## Current System

### Components

| Component | Purpose |
|-----------|---------|
| `StageTracking` table | Per-item, per-stage status with retries and timestamps |
| `upsert_done/failed/skipped` | Idempotent recording helpers (use `ON CONFLICT DO UPDATE`) |
| `retry_filter` | SQL clause: failed + under max retries + cooldown passed |
| Stage queries | Each job queries for items needing processing via `LEFT JOIN StageTracking` |

### Stage inventory

| Stage | Job file | Source type | Tracking key |
|-------|----------|-------------|--------------|
| `DOWNLOAD` | `webpage/job.py` | `webpage` | `canonical_url` |
| `TRANSCRIBE` | `transcription/job.py` | `youtube` | `external_id` |
| `DISCUSSION_SEARCH` | `discussion_search/job.py` | `webpage` | `canonical_url` |
| `COMMENTS` | `collectors/*/collector.py` | per-source | `external_id` |

## Bugs Found and Fixed

| Stage | File | Bug | Fix |
|-------|------|-----|-----|
| DOWNLOAD | `webpage/job.py` | `bronze_exists_by_url` outside try/except | Fixed in earlier commit |
| TRANSCRIBE | `transcription/job.py` | Duration check (`json.loads`), cache check (`read_bronze_or_none`), cached result handling all outside try/except | Moved inside try/except |
| TRANSCRIBE | `transcription/job.py` | `future.result()` unguarded — one thread crash kills batch | Added per-future try/except with tracking |
| DISCUSSION_SEARCH | `discussion_search/job.py` | `upsert_done`/`upsert_failed` outside try/except — DB error during tracking write crashes batch | Wrapped per-item block in outer try/except |
| COMMENTS (HN) | `hackernews/collector.py` | `write_bronze` + `_mark_comments_done` outside try/except | Moved inside try/except |
| COMMENTS (Reddit) | `reddit/collector.py` | Same pattern | Moved inside try/except |
| COMMENTS (Lobsters) | `lobsters/collector.py` | Same pattern | Moved inside try/except |

## Future Direction: Framework Options

### Option A: `run_stage` helper (inversion of control)

A ~60-line helper that wraps per-item processing:

```python
def run_stage(engine, source, stage, items, *, key_fn, process_fn):
    """Process items with guaranteed tracking. process_fn returns or raises."""
    for item in items:
        ext_id = key_fn(item)
        try:
            result = process_fn(item)
            if result is None:
                upsert_skipped(engine, source, ext_id, stage, "skipped")
            else:
                upsert_done(engine, source, ext_id, stage)
        except Exception:
            logger.exception("stage.item_failed stage=%s id=%s", stage, ext_id)
            upsert_failed(engine, source, ext_id, stage, traceback.format_exc())
```

**Pro:** Simple, works today, zero architecture change. The try/except is in one place — developers can't forget it.

**Con:** Parallel to Dagster. Custom framework to maintain. Doesn't help with batch-level concerns (connection pooling, progress reporting).

### Option B: Dagster DynamicOutput

Per-item ops with native Dagster visibility:

```python
@op(out=DynamicOut())
def fan_out(context, items):
    for item in items:
        yield DynamicOutput(item, mapping_key=item.id)

@op
def process_one(context, item):
    ...  # Dagster handles retries, tracking, UI

fan_out().map(process_one).collect()
```

**Pro:** Native Dagster UI shows per-item status, retries, timing. No custom tracking table needed.

**Con:** Fundamental architecture change — stages are currently decoupled jobs on independent schedules. This requires a single graph per pipeline.

**Scale concern:** We have thousands of YouTube videos and hundreds of thousands of content items, approaching millions. Each DynamicOutput creates a Dagster event. At 100k+ items per batch, the event log grows linearly, degrading webserver and daemon performance.

### Option C: Dagster assets with dynamic partitions

Each URL/external_id becomes a partition key:

```python
youtube_partitions = DynamicPartitionsDefinition(name="youtube_videos")

@asset(partitions_def=youtube_partitions)
def transcribed_video(context):
    ext_id = context.partition_key
    ...
```

**Pro:** Dagster tracks materialization per partition across all assets. Native backfill, status, lineage.

**Con:** Same scale concern as Option B. Dagster stores partition metadata in the event log. At 100k+ partitions, the webserver pagination queries slow down significantly, and the daemon's partition evaluation loop becomes a bottleneck. The Dagster team explicitly warns against this scale for dynamic partitions.

### Recommendation

**Option A (`run_stage`) is the pragmatic choice** for our scale. It provides the completeness invariant at the database level with minimal code change. The custom tracking table is actually an advantage — it's a simple PostgreSQL table that handles millions of rows trivially, unlike Dagster's event log.

Dagster DynamicOutput (Option B) could be explored for bounded batches (e.g., discussion search processes ~50 items per run) where UI observability is valuable, but should not be used for high-volume stages like content download or transcription.

Option C is off the table at our current scale trajectory.
