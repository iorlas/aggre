# Hatchet Operations Guide

Cookbook-style recipes for interacting with Hatchet from scripts and Claude Code sessions.

## Connection Setup

```python
import os
os.environ["HATCHET_CLIENT_TLS_STRATEGY"] = "none"

from aggre.workflows import get_hatchet
h = get_hatchet()
```

**Required env:**
- `HATCHET_CLIENT_TOKEN` — stored in `.env` (gitignored). Get from Hatchet UI at http://localhost:8888 → Settings → API Tokens
- `HATCHET_CLIENT_TLS_STRATEGY=none` — required for local dev (hatchet-lite uses insecure gRPC)

The `get_hatchet()` singleton reads env vars automatically. The token is in `.env` (loaded by docker-compose but NOT by bare `uv run` — need to source it or set it).

**Running scripts locally:**
```bash
source .env && HATCHET_CLIENT_TLS_STRATEGY=none uv run python script.py
```

## Key Operations via `h.runs` (RunsClient)

### List runs by status

```python
from datetime import datetime, timedelta, timezone
from hatchet_sdk.clients.rest.models.v1_task_status import V1TaskStatus

failed = h.runs.list(
    since=datetime.now(tz=timezone.utc) - timedelta(days=1),
    statuses=[V1TaskStatus.FAILED],
)
print(f"{len(failed.rows)} failed runs")
for r in failed.rows:
    print(f"  {r.metadata.id} {r.display_name} {r.created_at}")
```

### Replay a single failed run

```python
h.runs.replay(run_external_id)
```

### Bulk replay all failed runs (with pagination)

```python
h.runs.bulk_replay_by_filters_with_pagination(
    since=datetime.now(tz=timezone.utc) - timedelta(days=7),
    statuses=[V1TaskStatus.FAILED],
)
```

### Cancel runs

```python
h.runs.cancel(run_external_id)

# Bulk cancel queued/running
h.runs.bulk_cancel_by_filters_with_pagination(
    since=datetime.now(tz=timezone.utc) - timedelta(days=1),
    statuses=[V1TaskStatus.QUEUED, V1TaskStatus.RUNNING],
)
```

### Trigger a new workflow run

```python
h.runs.create("process-transcription", {
    "content_id": 123,
    "discussion_id": 456,
    "source": "youtube",
    "domain": "youtube.com",
})
```

### Push events (fan-out to all subscribers)

```python
from hatchet_sdk.clients.events import PushEventOptions
from aggre.workflows.models import ItemEvent

event = ItemEvent(content_id=123, discussion_id=456, source="youtube", domain="youtube.com")
h.event.push("item.new", event.model_dump(), options=PushEventOptions(scope="default"))
```

### Get run details

```python
run = h.runs.get(external_id)
# Returns full details including task outputs and errors
```

## Common Operational Recipes

### Retry all failed transcriptions from last 7 days

Use `bulk_replay_by_filters_with_pagination` with `workflow_ids` filter to target specific workflows.

### Clear stuck queued runs

```python
h.runs.bulk_cancel_by_filters_with_pagination(
    since=datetime.now(tz=timezone.utc) - timedelta(days=1),
    statuses=[V1TaskStatus.QUEUED],
)
```

### Backfill unprocessed items

Query DB for items needing processing, push `item.new` events. See `scripts/backfill_transcription.py` for an example.

### Replay/backfill safely (avoid schedule timeout floods)

Bulk replay or backfill pushes all runs into the queue at once. With concurrency limits (e.g., `max_runs=1` per domain), runs serialize and the back of the queue can wait a long time. Even with our 72h `schedule_timeout`, flooding thousands of runs creates unnecessary queue pressure.

**Batch replays in groups of 10-20 with a short delay:**

```python
import time

runs_to_replay = [...]  # list of workflow_run_external_ids
BATCH = 10
for i in range(0, len(runs_to_replay), BATCH):
    batch = runs_to_replay[i:i + BATCH]
    for ext_id in batch:
        h.runs.replay(ext_id)
    print(f"Replayed {min(i + BATCH, len(runs_to_replay))}/{len(runs_to_replay)}")
    time.sleep(5)  # let the queue breathe
```

For large backfills (hundreds+), use the same batching approach with `h.event.push()`.

## Timeout Design

Event-triggered workflows use `schedule_timeout="72h"` — time a task can wait in the concurrency queue before being scheduled onto a worker. Set high because:
- Concurrency limits serialize runs (e.g., 1 per domain), so backlogs are normal
- Worker restarts during development create queue buildup
- System may be intentionally stopped for days

Cron-triggered workflows (collectors) keep the default 5m — they run on schedule, not from event queues.

`execution_timeout` is separate — how long a task can run once it starts executing.

## Status Reference

| Status | Meaning |
|--------|---------|
| `QUEUED` | Waiting for worker slot |
| `RUNNING` | Currently executing |
| `COMPLETED` | Finished successfully |
| `FAILED` | Failed (will retry if retries remain) |
| `CANCELLED` | Manually or programmatically cancelled |

## Discovery Protocol for New Use Cases

When encountering a Hatchet operation not covered here:

1. **Don't reinvent** — check this guide for a similar recipe first
2. **Research the SDK** — read the relevant SDK source in `.venv/lib/python3.12/site-packages/hatchet_sdk/features/` (especially `runs.py`) and `hatchet_sdk/hatchet.py` for available client properties: `h.runs`, `h.cron`, `h.filters`, `h.logs`, `h.metrics`, `h.workers`, `h.workflows`, `h.scheduled`, `h.webhooks`
3. **Document it** — add the new recipe to this guide before using it
4. **Then execute** — only run the operation after steps 1-3
