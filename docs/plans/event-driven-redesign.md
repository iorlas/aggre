# Event-Driven Per-Item Redesign — Research

**Date:** 2026-03-08
**Status:** Research complete, pending decision

## Context

Step 1 (shipped): downstream workflows switched from `on_events=["content.new"]` to `on_crons`. This fixes batch orphaning and race conditions. The question now is whether to go further — per-item event-driven processing instead of batch cron.

## Current Architecture (post Step 1)

```
collect-{source} (cron) --> writes to DB
webpage          (cron */15) --> queries DB for unprocessed, batch downloads+extracts
transcription    (cron 0 */2) --> queries DB for unprocessed YouTube, batch transcribes
comments         (cron */30) --> queries DB for unprocessed, batch fetches
discussion-search (cron */30) --> queries DB for unprocessed, batch searches
```

All downstream workflows poll the DB for work. No events between them.

## Proposed Architecture (per-item event-driven)

```
collect-{source} (cron) --> for each new item:
  |                           workflow.run_no_wait(ProcessItem(url=..., source=...))
  |
process-webpage    (event per item, concurrency max_runs=5)
process-transcription (event per item, concurrency max_runs=2)
process-comments     (event per item, concurrency max_runs=3)
process-discussion-search (event per item, concurrency max_runs=2)
```

Each item gets its own workflow run. Hatchet manages queuing, retry, concurrency.

---

## Research Findings

### 1. Hatchet Concurrency API

Hatchet provides workflow-level concurrency via `ConcurrencyExpression`:

```python
from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy

wf = hatchet.workflow(
    name="process-webpage",
    concurrency=ConcurrencyExpression(
        expression="input.source",       # CEL expression for grouping key
        max_runs=5,                      # max concurrent runs PER KEY
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
    input_validator=ProcessItemInput,
)
```

**Key points:**
- `max_runs` limits concurrent runs per concurrency group key
- `expression` is a CEL expression against the workflow input
- Excess runs are **queued** (not rejected) with `GROUP_ROUND_ROBIN`
- Multiple concurrency expressions per workflow are supported

**Strategies:**
| Strategy | Behavior | Use case |
|----------|----------|----------|
| `GROUP_ROUND_ROBIN` | Queue excess, dequeue fairly across groups | Default for processing |
| `CANCEL_IN_PROGRESS` | Cancel oldest running to make room | Only latest matters |
| `CANCEL_NEWEST` | Reject new arrivals when full | Protect active work |

Worker-level concurrency (`slots=20`) is separate — controls how many tasks a single worker process runs concurrently across all workflows.

### 2. Hatchet Internal DB — Grafana Queryability

Hatchet stores everything in PostgreSQL. It has **OLAP tables** designed for monitoring:

| Table | What it tracks |
|-------|----------------|
| `v1_tasks_olap` | Task executions with `readable_status` (QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED) |
| `v1_runs_olap` | Workflow run status |
| `v1_task_events_olap` | Task lifecycle events with `error_message` and `output` |
| `v1_log_line` | Task log output |
| `v1_retry_queue_item` | Tasks waiting for retry with `retry_after` |

**Grafana integration:** Connect Grafana to Hatchet's PostgreSQL directly. Example queries:

```sql
-- Stuck runs (RUNNING > 1 hour)
SELECT external_id, workflow_id, display_name, inserted_at
FROM v1_tasks_olap
WHERE readable_status = 'RUNNING'
  AND inserted_at < NOW() - INTERVAL '1 hour';

-- Failed runs in last 24h with errors
SELECT t.external_id, t.display_name, e.error_message
FROM v1_tasks_olap t
JOIN v1_task_events_olap e ON e.task_id = t.id
WHERE t.readable_status = 'FAILED'
  AND t.inserted_at > NOW() - INTERVAL '24 hours';

-- Run status distribution
SELECT readable_status, COUNT(*)
FROM v1_runs_olap
WHERE inserted_at > NOW() - INTERVAL '24 hours'
GROUP BY readable_status;
```

Tables are range-partitioned by timestamp for efficient queries.

### 3. StageTracking Replacement Assessment

**Current StageTracking:** Tracks per-item processing stages (DOWNLOAD, EXTRACT, TRANSCRIBE, DISCUSSION_SEARCH) with retry counts, error messages, cooldown timestamps in our own DB.

**What Hatchet already tracks per workflow run:**
- Status (queued/running/completed/failed/cancelled)
- Retry count and retry-after
- Error messages
- Task output (JSON)
- Log lines
- Duration / timestamps

**Assessment:**

| StageTracking feature | Hatchet equivalent | Gap? |
|-----------------------|-------------------|------|
| Per-item status | `v1_tasks_olap.readable_status` | No |
| Retry count | Built-in retry with backoff | No |
| Error message | `v1_task_events_olap.error_message` | No |
| Cooldown before retry | `v1_retry_queue_item.retry_after` | No |
| Skip tracking | Task completes with "skipped" output | Minor — need convention |
| Stage within workflow | Parent-child task status | No |
| Grafana dashboards | Query Hatchet DB instead of Aggre DB | Migration needed |

**Conclusion:** If we go per-item, StageTracking becomes redundant. Hatchet tracks everything we need. The main migration cost is rewriting Grafana dashboards to query Hatchet's DB instead of our `stage_tracking` table.

**Risk:** Coupling monitoring to Hatchet's internal schema. If Hatchet changes table structure in an upgrade, dashboards break. Mitigation: use Hatchet's OLAP tables (they're designed for external consumption) and pin Hatchet version.

### 4. Per-Item Workflow Cost

**200 items = 200 workflow runs — is that fine?**

Yes. Hatchet benchmarks on 8-CPU RDS:

| Throughput | DB CPU | Avg execution time |
|------------|--------|--------------------|
| 100 runs/s | 15% | ~40ms |
| 500 runs/s | 60% | ~48ms |
| 2,000 runs/s | 83% | ~220ms |

200 runs arriving at once is trivial. Our collectors run hourly, so burst is 200 items/hour — well within Hatchet's capacity even on minimal hardware.

Worker slots (`slots=20`) mean 20 tasks execute concurrently. 200 items would queue and process 20 at a time. With workflow concurrency limits (e.g., `max_runs=2` for transcription), processing is further throttled per-workflow.

### 5. Triggering Workflows Programmatically

```python
# Fire-and-forget (from collector)
run_ref = workflow.run_no_wait(MyInput(url="...", source="hackernews"))

# Bulk (more efficient for many items)
results = await workflow.aio_run_many_no_wait([
    workflow.create_bulk_run_item(MyInput(url=url, source=src))
    for url, src in items
])

# Child workflow from parent task
@parent_wf.task()
async def fan_out(input, ctx):
    await child_wf.aio_run_many([
        child_wf.create_bulk_run_item(ChildInput(item_id=id))
        for id in input.item_ids
    ])
```

---

## Proposed Workflow Topology

### Naming Convention

| Pattern | Purpose | Example |
|---------|---------|---------|
| `collect-{source}` | Cron collector (parent) | `collect-hackernews` |
| `collect-{source}-feed` | Per-feed child (fan-out) | `collect-rss-feed` |
| `process-webpage` | Per-item webpage processing | download -> extract DAG |
| `process-transcription` | Per-item YouTube transcription | download audio -> transcribe |
| `process-comments` | Per-item comment fetching | fetch comments for one discussion |
| `process-discussion-search` | Per-item discussion search | search HN+Lobsters for one URL |

### Concurrency Limits

| Workflow | `max_runs` | Strategy | Reason |
|----------|-----------|----------|--------|
| `process-webpage` | 5 | `GROUP_ROUND_ROBIN` | Spread across domains |
| `process-transcription` | 2 | `GROUP_ROUND_ROBIN` | YouTube IP ban risk, CPU-bound whisper |
| `process-comments` | 3 | `GROUP_ROUND_ROBIN` | Rate limits on Reddit/HN/Lobsters |
| `process-discussion-search` | 2 | `GROUP_ROUND_ROBIN` | HN/Lobsters rate limits |

### Routing

Collectors know which downstream workflow to trigger based on the item:

```python
# In collect task, after processing each discussion:
if source == "youtube":
    await process_transcription.aio_run_no_wait(TranscribeInput(content_id=content.id))
else:
    await process_webpage.aio_run_no_wait(WebpageInput(content_id=content.id))

# Always trigger for all items:
await process_comments.aio_run_no_wait(CommentsInput(discussion_id=disc.id))
await process_discussion_search.aio_run_no_wait(SearchInput(content_id=content.id))
```

---

## Rate Limit Constraints

| Resource | Limit | Why |
|----------|-------|-----|
| YouTube downloads | max 2 concurrent | IP ban risk |
| Reddit API | max 1 req/sec | Rate limit headers, 429s |
| HN API | max 1 req/sec | Observed throttling |
| Lobsters | max 1 req/sec | Small site, be polite |
| Webpage downloads | max 5 concurrent | Spread across domains |
| Whisper CPU | max 2 concurrent | CPU-bound |
| Hatchet worker slots | 20 total | Current config |

---

## Migration Path

### Phase 1 (done): Cron-only downstream
- Remove `on_events`, add `on_crons` to downstream workflows
- Remove event publishing from collectors
- No behavior change — same batch polling, just triggered by cron instead of events

### Phase 2: Per-item webpage processing
- Create `ProcessWebpageInput(content_id: int)` model
- Refactor `download_content` and `extract_html_text` to operate on single items
- Add `ConcurrencyExpression(max_runs=5)` to webpage workflow
- Collectors trigger `process-webpage.run_no_wait()` per new item
- Keep StageTracking for now (parallel tracking during validation)

### Phase 3: Per-item transcription + comments
- Same pattern for transcription (single-item transcribe)
- Same pattern for comments (single-discussion comment fetch)
- Add concurrency limits per workflow

### Phase 4: Remove StageTracking
- Verify Grafana dashboards work against Hatchet OLAP tables
- Remove StageTracking model, ops, and status modules
- Remove retry_filter queries from workflow functions
- Simplify workflow functions (no longer need batch query + loop)

---

## Cost/Benefit Summary

### Benefits
- **Immediate processing** — items processed seconds after collection, not waiting for next cron cycle (up to 30 min delay)
- **Per-item visibility** — each item is a workflow run in Hatchet UI, with its own logs, status, retry history
- **Natural concurrency control** — `max_runs` replaces manual ThreadPoolExecutor and batch sizing
- **Eliminate StageTracking** — ~500 lines of custom state management code removed
- **Simpler workflow functions** — process one item instead of query+loop over batch
- **Better error isolation** — one item failing doesn't affect others in the batch

### Costs
- **More Hatchet DB load** — 200 workflow runs vs 1 batch run per cycle (but benchmarks show this is trivial)
- **Routing complexity** — collectors must know which downstream workflow to trigger
- **Grafana dashboard migration** — rewrite queries from StageTracking to Hatchet OLAP tables
- **Hatchet schema coupling** — monitoring depends on Hatchet's internal tables
- **Migration effort** — refactor batch functions to single-item, estimated 2-3 days

### Recommendation

Go for it, but incrementally. The immediate latency win (seconds vs 15-30 min) and per-item visibility justify the effort. Start with webpage (Phase 2) as a proof of concept, then extend to other workflows.
