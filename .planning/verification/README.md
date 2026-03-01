# TLA+ Formal Verification for Aggre Pipeline

Formal verification specs for the Aggre content aggregation pipeline, written in PlusCal/TLA+ and checked with the TLC model checker.

## Specs

### NullCheckQueue.tla
Reusable abstract module capturing the shared null-check queue pattern used across all Aggre pipeline stages:
- Items start with nullable fields (null = needs processing)
- Sensor singleton guard (at most one active job)
- Batch query: `WHERE field IS NULL AND error IS NULL`
- Atomic per-item updates via `update_content()`

This module defines the pattern but is not directly model-checked; the concrete pipeline specs below instantiate it.

### ContentPipeline.tla
Models the two-phase webpage pipeline from `dagster_defs/webpage/job.py`:
- **Phase 1 (Download)**: Parallel workers (ThreadPoolExecutor) query `WHERE text IS NULL AND error IS NULL AND fetched_at IS NULL`, download HTML, atomically set `fetched_at` (or `error`)
- **Phase 2 (Extract)**: Sequential processing queries `WHERE text IS NULL AND error IS NULL AND fetched_at IS NOT NULL`, extracts text, sets `text` (or `error`)
- **Sensor**: Singleton guard prevents concurrent job runs

### EnrichmentPipeline.tla
Models the enrichment pipeline from `dagster_defs/enrichment/job.py`:
- Sequential processing with multi-platform search (HN + Lobsters)
- Models the **known partial failure bug**: if either platform fails, `enriched_at` stays NULL and the item is re-queried indefinitely
- Uses `PermanentHNFailItem` constant to model a URL that always fails on HN

## Model Configuration

| Spec | Items | Workers | Notes |
|------|-------|---------|-------|
| ContentPipeline | 3 | 2 ({"w1","w2"}) | Small for tractability |
| EnrichmentPipeline | 3 | 1 (sequential) | PermanentHNFailItem=1 |

## Properties Verified

### ContentPipeline (ContentPipeline.cfg)

| Property | Type | Result | Description |
|----------|------|--------|-------------|
| NoDoubleProcessing | Safety (invariant) | PASS | No item processed by two download workers simultaneously |
| PhaseOrder | Safety (invariant) | PASS | text can only be non-null if fetched_at is also non-null |
| MutualExclusion | Safety (invariant) | PASS | text="extracted" and error="error" never both set |
| SensorGuard | Safety (invariant) | PASS | If workers are busy, job must be running |
| AllComplete | Liveness (temporal) | PASS | Every item eventually reaches a terminal state |
| MonotonicText | Liveness (temporal) | PASS | Once text is set, it never reverts to null |
| MonotonicError | Liveness (temporal) | PASS | Once error is set, it never reverts to null |
| MonotonicFetchedAt | Liveness (temporal) | PASS | Once fetched_at is set, it never reverts to null |

### EnrichmentPipeline

**Bug detection run (EnrichmentPipeline.cfg):**

| Property | Type | Result | Description |
|----------|------|--------|-------------|
| SensorGuard | Safety (invariant) | PASS | If processing, job must be running |
| NoInfiniteReprocess | Safety (invariant) | **VIOLATED** | Item 1 processed 3 times (limit was 2) -- confirms the partial failure bug |

**Safe properties run (EnrichmentPipeline_safe.cfg, with StateConstraint):**

| Property | Type | Result | Description |
|----------|------|--------|-------------|
| SensorGuard | Safety (invariant) | PASS | If processing, job must be running |
| MonotonicEnriched | Liveness (temporal) | PASS | Once enriched, stays enriched |

## TLC Results

### ContentPipeline
```
16,120 states generated, 5,430 distinct states found
State graph depth: 38
Time: ~1 second
Result: ALL PROPERTIES VERIFIED
```

### EnrichmentPipeline (bug detection)
```
820 states generated, 649 distinct states found
State graph depth: 48
Time: <1 second
Result: NoInfiniteReprocess VIOLATED at state 38
```

### EnrichmentPipeline (safe properties)
```
2,568 states generated, 1,997 distinct states found
State graph depth: 89
Time: ~2 seconds (with StateConstraint bounding processCount <= 4)
Result: ALL PROPERTIES VERIFIED
```

## Bug Counterexample (Enrichment Partial Failure)

TLC found a 38-state counterexample trace for the NoInfiniteReprocess violation:

1. **States 1-5**: Sensor starts job, worker begins processing batch {1, 2, 3}
2. **States 6-9**: Worker picks item 1, HN search **fails** (permanent failure), Lobsters succeeds. Since `failed=True`, `enriched_at` stays NULL. Item 1 is NOT marked as enriched.
3. **States 10-20**: Worker processes items 2 and 3 successfully (both enriched)
4. **States 21-25**: Job completes. Sensor detects item 1 still needs enrichment. Starts new job with batch {1}.
5. **States 26-31**: Worker picks item 1 again (processCount=2). HN fails again. Still not enriched.
6. **States 32-38**: Job completes. Sensor starts ANOTHER job. Worker picks item 1 (processCount=3). **INVARIANT VIOLATED**: processCount[1] = 3 > 2.

This confirms the real bug in `enrichment/job.py`: when one platform fails, the `failed` flag prevents `enriched_at` from being set, but no error is recorded either, so the item is re-queried on every subsequent job run forever.

### Fix suggestion
Set `enriched_at` with an error marker (or a separate `enrichment_error` column) even on partial failure, so the item exits the processing queue:
```python
if not failed:
    update_content(engine, row.id, enriched_at=now_iso())
else:
    update_content(engine, row.id, enriched_at=now_iso(), error="enrichment_partial_failure")
```

## Running

```bash
# Run all specs
./run.sh all

# Run individual specs
./run.sh content
./run.sh enrichment
```

Requires the TLA+ Toolbox installed at `/Applications/TLA+ Toolbox.app/`.

## Files

- `NullCheckQueue.tla` -- Abstract null-check queue pattern (reusable module)
- `ContentPipeline.tla` -- Content download + extraction spec (PlusCal)
- `ContentPipeline.cfg` -- TLC config: 3 items, 2 workers
- `EnrichmentPipeline.tla` -- Enrichment spec with partial failure bug (PlusCal)
- `EnrichmentPipeline.cfg` -- TLC config: bug detection (NoInfiniteReprocess)
- `EnrichmentPipeline_safe.cfg` -- TLC config: safe properties only (with StateConstraint)
- `EnrichmentPipeline_liveness.cfg` -- TLC config: AllEnriched liveness check
- `run.sh` -- Translates PlusCal and runs TLC for all specs
