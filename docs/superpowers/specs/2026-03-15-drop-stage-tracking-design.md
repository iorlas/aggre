# Drop Stage Tracking — Replace with Silver Timestamps

## Problem

The `stage_tracking` table duplicates what Hatchet already provides (retry counts, cooldowns, failure tracking, in-progress state). Meanwhile, the Silver tables lack timestamps for two operations whose completion can't be inferred from data columns alone: discussion search and comment fetching.

## Decision

Remove `stage_tracking` entirely. Add two timestamps to Silver tables for operations where "did we do it?" and "when?" can't be answered from existing data columns. Delete legacy batch comment methods that only exist for the pre-Hatchet code path.

## What Changes

### Schema Changes

| Table | Column | Action | Rationale |
|---|---|---|---|
| `silver_content` | `discussions_searched_at` | **Add** (text, nullable) | No data column changes on SilverContent when discussions are searched — results go to `silver_discussions`. This is load-bearing: NULL = never searched, non-null = searched + when. |
| `silver_discussions` | `fetched_at` | **Keep as-is** | Means "when we fetched this discussion from the source API." Semantically distinct from `created_at` — carries domain meaning (collection time from external source). |
| `silver_discussions` | `comments_fetched_at` | **Add** (text, nullable) | Enables staleness-based comment re-fetching. `comments_json IS NOT NULL` tells you it was done; this tells you when, so you can re-fetch after N days. |
| `stage_tracking` | — | **Drop table** | Fully replaced by Hatchet (retries, failures, in-progress) + Silver timestamps (completion). |

### Index Changes

- **Drop** `idx_stage_actionable` (on `stage_tracking`) — table is dropped.
- **Replace** `idx_content_needs_discussion_search` — currently filters on `text IS NOT NULL AND canonical_url IS NOT NULL`. Replace with `idx_content_needs_discussion_search` on `silver_content(id)` where `discussions_searched_at IS NULL AND text IS NOT NULL`. This combines "has text to search for" with "hasn't been searched yet." The old index becomes redundant.
- **Keep** `idx_discussions_comments_null` as-is — `comments_json IS NULL` remains the null-check for "needs comment fetching." `comments_fetched_at` is for staleness re-fetch, which is a separate query (no index needed until staleness workflows are built).

### Why NOT Other Timestamps

| Considered | Decision | Reason |
|---|---|---|
| `downloaded_at` on `silver_content` | Skip | Download is an intermediate step in the download→extract Hatchet DAG. Output lives in bronze (S3/filesystem), not Silver. No consumer would query this. Re-downloading (for post edits) would be event-driven, not staleness-based. |
| `extracted_at` on `silver_content` | Skip | `text IS NOT NULL` already answers "was it extracted?" No staleness use case — you don't re-extract the same HTML. |
| `transcribed_at` on `silver_content` | Skip | `text IS NOT NULL` already answers "was it transcribed?" `transcribed_by` column already distinguishes transcription from extraction. No re-transcription use case. |

### How "Did We Do It?" Is Answered Per Workflow

| Workflow | "Done?" signal | "When?" signal | "In progress / failed?" |
|---|---|---|---|
| Download + Extract | `text IS NOT NULL` (data) | Not tracked (no consumer) | Hatchet run state |
| Transcribe | `text IS NOT NULL` (data) | Not tracked (no consumer) | Hatchet run state |
| Discussion Search | `discussions_searched_at IS NOT NULL` (timestamp) | `discussions_searched_at` value | Hatchet run state |
| Comments | `comments_json IS NOT NULL` (data) | `comments_fetched_at` value | Hatchet run state |

### How "Needs Retry?" Is Answered

Hatchet owns all retry logic. Each workflow is configured with `retries=7, backoff_factor=4, backoff_max_seconds=3600`. The `stage_tracking` retry filter, max retries, and cooldown logic are deleted.

### Staleness Re-Processing

For discussion search and comments, staleness queries become:

```sql
-- Content needing discussion re-search (older than 7 days)
SELECT id FROM silver_content
WHERE discussions_searched_at IS NOT NULL
  AND discussions_searched_at::timestamptz < now() - interval '7 days';

-- Discussions needing comment refresh (older than 3 days)
SELECT id FROM silver_discussions
WHERE comments_fetched_at IS NOT NULL
  AND comments_fetched_at::timestamptz < now() - interval '3 days';
```

These would be triggered by future scheduled workflows, not by the current event-driven `item.new` system.

## Code Changes

### Delete

- `src/aggre/tracking/` — entire module (`model.py`, `ops.py`, `status.py`, `__init__.py`)
- `collect_comments()` batch methods on each collector (`hackernews`, `reddit`, `lobsters`) — legacy pre-Hatchet code, only called from tests. The Hatchet workflow uses `fetch_discussion_comments()` (per-item) instead.
- `_query_pending_comments()` on `BaseCollector` — only used by deleted `collect_comments()` batch methods.
- `_mark_comments_failed()` on `BaseCollector` — Hatchet handles failure tracking.

### Modify

- **`src/aggre/db.py`**:
  - `SilverContent`: add `discussions_searched_at` column
  - `SilverDiscussion`: add `comments_fetched_at` column (keep `fetched_at` as-is)
  - Replace index `idx_content_needs_discussion_search` with new version filtering on `discussions_searched_at IS NULL AND text IS NOT NULL`

- **`src/aggre/collectors/base.py`**:
  - Remove imports of `StageTracking`, `retry_filter`, `upsert_done`, `upsert_failed`, `Stage`
  - `_mark_comments_done()`: remove `upsert_done()` call, set `comments_fetched_at` instead

- **`src/aggre/collectors/{hackernews,reddit,lobsters}/collector.py`**:
  - Delete `collect_comments()` method
  - `fetch_discussion_comments()` — this is the production code path (called by Hatchet workflow). It calls `_mark_comments_done()`, which is updated above. No other changes needed.

- **`src/aggre/workflows/discussion_search.py`**:
  - After successful search, set `discussions_searched_at` on `SilverContent`

- **`docs/guidelines/semantic-model.md`**: update schema to reflect new columns, remove `stage_tracking` references

### Tests

- **Delete**: `tests/tracking/test_invariants.py`, `tests/tracking/test_ops.py` — test the deleted tracking module
- **Update**: `tests/conftest.py` — remove `aggre.tracking.model` import
- **Update**: `tests/helpers.py` — remove `assert_tracking_status` and `assert_no_tracking` helpers
- **Update**: `tests/test_acceptance_cli.py` — remove `stage_tracking` table assertions
- **Rewrite**: `tests/collectors/test_{hackernews,reddit,lobsters}.py` — remove all `collect_comments` tests, update `fetch_discussion_comments` tests to assert `comments_fetched_at` instead of stage tracking

### Migration

Single Alembic migration:
1. Add `discussions_searched_at` to `silver_content`
2. Add `comments_fetched_at` to `silver_discussions`
3. Replace index `idx_content_needs_discussion_search`
4. Backfill `comments_fetched_at = now()` for rows where `comments_json IS NOT NULL`
5. Drop `stage_tracking` table (and `idx_stage_actionable`)

### Data Backfill (in migration)

- `discussions_searched_at`: leave NULL. Items will be re-searched on next `item.new` event — harmless, just redundant work for one cycle.
- `comments_fetched_at`: set to `now()` for all rows where `comments_json IS NOT NULL`. This must happen in the migration (before any staleness query runs) to prevent immediate re-fetch of all existing comments.

## Behavioral Change: Previously-Failed Items

Items that exhausted their retries in `stage_tracking` (e.g., `status='failed', retries=3`) currently have `comments_json IS NULL` and are excluded by the `retry_filter` join. After dropping `stage_tracking`, these items will have `comments_json IS NULL` and `comments_fetched_at IS NULL`, making them eligible for processing again via Hatchet.

This is acceptable: Hatchet's own retry policy (7 retries with exponential backoff) will handle them. If they fail again, Hatchet marks the run as failed and stops. The item stays with `comments_json IS NULL` and won't be retried until a new `item.new` event is pushed.

## What This Does NOT Change

- Null-check pattern for "needs processing" — unchanged
- Hatchet workflow structure — unchanged
- Bronze storage — unchanged
- `Source.last_fetched_at` — unchanged (collector TTL logic)
- Event-driven `item.new` triggering — unchanged
- `SilverDiscussion.fetched_at` — unchanged (keeps its domain meaning)
