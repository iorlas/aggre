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

## Resetting the Admin UI Password

`SEED_DEFAULT_ADMIN_PASSWORD` in compose is **one-shot** — it only seeds the account on first deploy and is ignored after that. Changing it in compose does NOT update the existing password.

To reset:

```bash
# 1. Generate a bcrypt hash — use Python to avoid shell escaping issues
uv run --with bcrypt python3 -c "
import bcrypt
h = bcrypt.hashpw(b'YourNewPassword', bcrypt.gensalt(10))
print(h.decode())
" > /tmp/new_hash.txt

# 2. Write the UPDATE to a SQL file — never interpolate bcrypt hashes in shell strings
#    (the $ signs get expanded as shell variables and the hash is silently corrupted)
python3 -c "
hash = open('/tmp/new_hash.txt').read().strip()
open('/tmp/reset_pw.sql','w').write(f'UPDATE \"UserPassword\" SET hash = \'{hash}\';')
"

# 3. Copy and execute via psql
scp -P 2201 /tmp/reset_pw.sql iorlas@shen.iorlas.net:/tmp/reset_pw.sql
ssh -p 2201 iorlas@shen.iorlas.net \
  "docker cp /tmp/reset_pw.sql compose-connect-back-end-alarm-zgu447-hatchet-postgres-1:/tmp/ && \
   docker exec compose-connect-back-end-alarm-zgu447-hatchet-postgres-1 psql -U hatchet -d hatchet -f /tmp/reset_pw.sql"

# 4. Verify login — use Python, not curl, to avoid ! mangling in shell
python3 -c "
import urllib.request, json
body = json.dumps({'email':'admin@example.com','password':'YourNewPassword'}).encode()
req = urllib.request.Request('http://hatchet.ts.shen.iorlas.net/api/v1/users/login',
    data=body, headers={'Content-Type':'application/json'}, method='POST')
import urllib.error
try:
    with urllib.request.urlopen(req) as r: print('OK', r.status)
except urllib.error.HTTPError as e: print('FAIL', e.code, e.read().decode())
"
```

**Pitfalls to avoid:**
- Never interpolate bcrypt hashes (`$2b$10$...`) in double-quoted shell strings — bash expands `$2b`, `$10`, etc. as variables, silently corrupting the hash
- Never use `curl -d '...'` with passwords containing `!` — shells escape `!` to `\!`, making Go's JSON parser reject the request with "invalid character '!' in string escape code"
- Prefer passwords without `!` for Hatchet admin to avoid this class of issue entirely

## Removing Workflows from Code

Hatchet workflow definitions persist in the database forever — they are NOT removed when a worker stops registering them. If you delete a workflow from code but not from Hatchet, it becomes a ghost: still subscribed to events (e.g., `item.new`), still spawning runs, but with no worker to execute them.

**When removing a workflow from code, also delete it from Hatchet UI:** Workflows → select → delete. Or via the SDK:

```python
# Find and delete a ghost workflow
workflows = h.workflows.list()
for w in workflows.rows:
    if w.name == "process-discussion-search":
        h.workflows.delete(w.metadata.id)
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

## Known Issue: Zombie Tasks (Tasks Stuck in RUNNING)

**Severity: Critical.** This is a systemic Hatchet architectural limitation, not a misconfiguration. It has caused full pipeline stalls multiple times.

### Symptoms

- Tasks show RUNNING in Hatchet UI for far longer than their `execution_timeout`
- No new tasks start — the entire pipeline stalls (downloads, transcriptions, comments)
- Any task type can become a zombie — transcription, download, comments
- Worker shows active heartbeat, but assigned tasks are not executing

### Root Cause

When the worker process dies (deploy, crash, OOM, SIGTERM), Hatchet does not reliably clean up in-flight tasks:

1. **Worker shutdown does not report task failure** ([#3308](https://github.com/hatchet-dev/hatchet/issues/3308)) — on SIGTERM, in-flight tasks are not marked as failed. They stay RUNNING until `execution_timeout` expires server-side — if it fires at all.

2. **gRPC stream dies silently** ([#3280](https://github.com/hatchet-dev/hatchet/issues/3280), OPEN) — the `ListenV2` gRPC stream can be killed by proxies or network issues. The engine dispatches tasks to the dead stream, marking them RUNNING, but the worker never receives them. Reassignment only catches tasks on workers with expired heartbeats, **not tasks dispatched to dead streams**.

3. **Heartbeat timeout kills the action listener** ([#2432](https://github.com/hatchet-dev/hatchet/issues/2432)) — when `DEADLINE_EXCEEDED` occurs during heartbeat, it interrupts the action listener loop. The worker never reports completion.

4. **OLAP replication lag** ([#2573](https://github.com/hatchet-dev/hatchet/issues/2573)) — tasks complete in the core DB but the OLAP/UI tables never update. Default: 5 retries then drops the write. The task shows RUNNING forever in the UI even though it finished internally.

5. **Stale worker records** ([#60](https://github.com/hatchet-dev/hatchet/issues/60)) — dead workers stay `is_active=true` with stale heartbeats. Heartbeat interval (4s) and liveness checks (5s) are hardcoded and not configurable.

6. **Reassignment requires retries > 0** — tasks at max retry count that get stuck are never reassigned. The 30-second reassignment visibility timeout is also hardcoded.

### Why This Blocks Everything

Zombie RUNNING tasks hold concurrency slots. With `max_runs=20` for transcription and 40 total worker slots, 20 zombies consume 50% of worker capacity. With `GROUP_ROUND_ROBIN` concurrency, blocked slots prevent scheduling across all groups.

### Observed Incident (2026-03-26)

Timeline reconstructed from Hatchet database:
- **23:00-00:06** — System healthy. Downloads, comments, extracts completing normally.
- **~00:06** — Worker died (deploy or crash). Downloads stopped completing.
- **00:26-00:44** — 110 transcription tasks created from earlier events. 20 assigned to the (now-dead) worker, all on retry #6 (final retry).
- **00:06-02:06** — No worker running for ~2 hours. 20 transcription zombies held all `max_runs=20` slots. 513 downloads, 98 transcriptions, 12 comments queued with no progress.
- **02:06** — New worker started (deploy). Zombies persisted — `execution_timeout=30m` had long expired but was never enforced.
- **02:40** — Manual investigation confirmed 20 RUNNING zombies, all 90+ minutes old. Deploy eventually cleared them.

### Mitigations

**Immediate (config changes):**

Set `HATCHET_CLIENT_LISTENER_V2_TIMEOUT=180` on the worker — forces gRPC stream reconnect every 3 minutes, preventing silent stream death ([#3280](https://github.com/hatchet-dev/hatchet/issues/3280) workaround).

**Required (external reaper):**

Hatchet's built-in timeout enforcement cannot be trusted. Build an external reaper that:
1. Queries for tasks RUNNING longer than 2x their `execution_timeout`
2. Cancels them via `h.runs.cancel()`
3. Runs as a Hatchet cron workflow or external cron

**Monitoring:**

Query the Hatchet database (via dblink from aggre postgres) to detect zombie tasks:

```sql
-- Find zombie tasks: RUNNING longer than their execution_timeout
SELECT * FROM dblink(
  'host=hatchet-postgres dbname=hatchet user=hatchet password=hatchet',
  'SELECT display_name, readable_status, inserted_at,
          round(extract(epoch from now() - inserted_at)/60::numeric, 1) as minutes_ago,
          latest_retry_count, latest_worker_id
   FROM v1_tasks_olap
   WHERE readable_status = ''RUNNING''
   AND inserted_at < now() - interval ''30 minutes''
   ORDER BY inserted_at ASC'
) AS t(display_name text, status text, inserted_at timestamptz,
       minutes_ago numeric, retry_count int, worker_id uuid);
```

**Database access setup:** The aggre postgres has `dblink` extension installed and is on the `aggre-internal` Docker network alongside `hatchet-postgres`, enabling cross-database queries without additional infrastructure.

### Related Hatchet Issues

| Issue | Status | Summary |
|-------|--------|---------|
| [#3308](https://github.com/hatchet-dev/hatchet/issues/3308) | Open | Worker doesn't report failure on SIGTERM |
| [#3280](https://github.com/hatchet-dev/hatchet/issues/3280) | Open | ListenV2 streams killed silently by proxies |
| [#2573](https://github.com/hatchet-dev/hatchet/issues/2573) | Closed | OLAP replication drops writes, tasks show RUNNING forever |
| [#2432](https://github.com/hatchet-dev/hatchet/issues/2432) | Closed | Heartbeat timeout breaks action listener |
| [#1996](https://github.com/hatchet-dev/hatchet/issues/1996) | Closed | Run stuck RUNNING after all tasks done |
| [#1022](https://github.com/hatchet-dev/hatchet/issues/1022) | Closed | Duplicate execution on timeout |
| [#322](https://github.com/hatchet-dev/hatchet/issues/322) | Stale | Heartbeat intervals not configurable |
| [#60](https://github.com/hatchet-dev/hatchet/issues/60) | Closed | Stale workers not cleaned up |

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
