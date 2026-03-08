# Orchestration Guidelines

## Why Hatchet

Aggre processes individual items (URLs, videos, discussions) through stages. This is **task-centric** orchestration. Dagster is **asset-centric** — it orchestrates jobs and data assets, not individual items.

This mismatch forced building a custom `StageTracking` system with per-item retry, cooldowns, and state management on top of Dagster. With 10+ new pipelines planned, the boilerplate compounds.

**Decision (2026-03-06):** Migrate to Hatchet. See research 030 in researches-cowork repo for full evaluation of 7 tools.

### Tool Elimination Summary

| Tool | Reason Eliminated |
|------|-------------------|
| Prefect | Self-hosted: memory leaks, DB bloat, mapped task failures lose inputs on restart |
| Temporal | No application logs in web UI — requires Loki+OTel stack (~6 containers) |
| Inngest | Self-hosting "not highest priority," SSPL license |
| Windmill | Wrong category (low-code platform, not workflow engine) |
| Celery | Old, basic UI (Flower), still needs custom StageTracking |
| Dagster | Wrong abstraction level — asset-centric, not task-centric |

### Why Hatchet Wins

- Postgres-only deployment (same infra footprint as Dagster)
- Built-in logs per task in UI
- Per-item retry with backoff
- Event-driven + DAG support in one tool
- MIT license, stable SDK (v1.28+)
- MCP server for AI agent monitoring

## Technology Decision Framework

| Pipeline Type | Tool | Why |
|---------------|------|-----|
| Hourly/daily data collection | Hatchet cron | Periodic trigger, monitor runs |
| Item-level processing with retry | Hatchet workflow | Per-item state, retry, concurrency |
| SQL materializations | dbt (triggered from Hatchet) | SQL-first, built-in lineage |
| AI agent workflows | Hatchet | Durable execution, fan-out/fan-in |

**Rule of thumb:** If you're asking "is my table up to date?" — use an asset orchestrator (Dagster/dbt). If you're asking "did my task succeed?" — use a task orchestrator (Hatchet).

## Architecture Pattern: Event-Driven Per-Item Processing

### Collection → Event → Processing

Collectors run on cron, discover new items, write them to DB, and emit `item.new` events. All downstream workflows subscribe to this event and self-filter based on the event payload.

```
collect-{source}  (cron) --> writes discussions + content to DB
                         --> emits "item.new" per item
                                |
                    +-----------+-----------+-----------+
                    |           |           |           |
              process-webpage  process-   process-    process-
                             transcription comments  discussion-search
```

### Event Payload (Outbox Pattern)

Events carry only IDs and concurrency grouping keys. Data stays in DB.

```python
class ItemEvent(BaseModel):
    content_id: int
    discussion_id: int
    source: str                # "hackernews", "reddit", etc.
    domain: str | None = None  # content domain
```

Subscribers query DB for full data (URL, title, etc.) at execution time.

### Self-Filtering

Each workflow checks the event payload and returns `{"status": "skipped"}` if the item doesn't apply:

- `process-webpage` — skips items in `SKIP_DOMAINS`
- `process-transcription` — skips non-YouTube items (`source != "youtube"`)
- `process-comments` — skips sources without comment support
- `process-discussion-search` — skips items in `DISCUSSION_SEARCH_SKIP_DOMAINS`

### Concurrency Control

Hatchet `ConcurrencyExpression` with `max_runs=1` per grouping key. Excess runs queue (`GROUP_ROUND_ROBIN`), never rejected.

| Workflow | Expression | max_runs | Effect |
|----------|-----------|----------|--------|
| `process-webpage` | `input.domain` | 1 | 1 download per domain. 50 domains = 50 parallel. |
| `process-transcription` | `'youtube'` | 1 | 1 YouTube download at a time. |
| `process-comments` | `input.source` | 1 | 1 comment fetch per source (HN, Reddit, Lobsters parallel). |
| `process-discussion-search` | `'search'` | 1 | 1 search at a time (each hits both HN + Lobsters APIs). |

### Within a Workflow: Explicit DAG

Steps within a single workflow are connected via explicit parent-child relationships:

```python
wf = hatchet.workflow(name="process-webpage", on_events=["item.new"])

@wf.task()
def download(input):
    # Fetch webpage content
    ...

@wf.task(parents=[download])
def extract(input):
    # Extract text from HTML
    ...
```

### Rules

1. **Within a workflow**: explicit DAG. Clear dependencies, easy debugging.
2. **Between workflows**: event-driven via `item.new`. Each workflow self-filters.
3. **Partial reruns**: emit `item.new` events for specific items via backfill CLI.
4. **Concurrency**: Hatchet manages queuing per domain/source — no custom ThreadPoolExecutor.
5. **Backfills**: emit `item.new` events — same pipeline, same concurrency controls. Business functions don't self-filter; routing is the Hatchet task layer's job.

## Operational Constraints

| Resource | Limit | Why |
|----------|-------|-----|
| YouTube downloads | max 1 concurrent | IP ban risk (Hatchet concurrency) |
| Reddit API | max 1 req/sec | Rate limit headers, 429s |
| HN API | max 1 req/sec | Observed throttling |
| Lobsters | max 1 req/sec | Small site, be polite |
| Webpage downloads | max 1 per domain | Hatchet concurrency on `input.domain` |
| Whisper CPU | max 1 concurrent | Hatchet concurrency on `'youtube'` |
| Hatchet worker slots | 20 total | Current config |

## Worker Scaling

**Current setup:** 1 worker, 20 slots. Parallelism comes from Hatchet's concurrency model — 20 tasks can run simultaneously, constrained by per-workflow `max_runs` limits.

**How parallelism works with 1 worker:**
- `slots=20` means up to 20 tasks execute concurrently in one process
- `max_runs=1` per concurrency group means 1 per domain/source, but many groups run in parallel (e.g. 15 different domains = 15 parallel webpage downloads)
- Collection crons, comment fetches, transcription, and search all share the 20 slots

**Horizontal scaling:**
- Hatchet SDK natively supports multiple workers with the same name
- Run N instances of `python -m aggre.workflows` — Hatchet server distributes tasks automatically
- Each instance gets its own `slots=20`, so 2 workers = 40 total concurrent tasks
- Concurrency limits (`max_runs`) are enforced server-side, not per-worker — still 1 per domain even with 10 workers
- No code changes needed — just `docker compose up --scale hatchet-worker=N`

**When to scale:**
- Queue depth growing (events backing up) → add workers
- CPU-bound tasks (Whisper transcription) bottlenecking → dedicated worker with higher slots
- Currently unnecessary — 20 slots handles the load from 8 sources

**Backfills:**
- Backfills emit `item.new` events for existing unprocessed content (E4)
- Events go through the same Hatchet queue → same concurrency controls apply
- Domain filtering lives at the Hatchet task level, not in business functions — by design. Business functions process whatever they're given; the routing layer decides what to send.

## Workflow Patterns

### Cron Collection

Each source has a collection workflow triggered on cron schedule:

```python
@hatchet.workflow(name="collect-hackernews", on_crons=["0 * * * *"])
```

The collection workflow calls the pure collector function, writes results to DB, and emits `item.new` events.

### Event-Driven Processing

Processing workflows subscribe to `item.new` events:

```python
wf = hatchet.workflow(
    name="process-webpage",
    on_events=["item.new"],
    concurrency=ConcurrencyExpression(
        expression="input.domain",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
    input_validator=ItemEvent,
)
```

### Resource Injection

No framework-specific resource wrappers. Direct function calls:

```python
from aggre.utils.db import get_engine
from aggre.config import load_config

engine = get_engine()
config = load_config()
```

## Testing Approach

### Unit Tests (95%+ coverage target)

Business logic lives in pure functions. Tests call these functions directly — no orchestration framework involved.

```python
# Business logic is framework-free
def download_one(engine: Engine, config: AppConfig, content_id: int) -> str:
    ...

# Test calls the function directly
def test_download_one(engine):
    result = download_one(engine, config, content_id)
    assert result == "downloaded"
```

### E2E Tests (workflow wiring)

Verify that workflows are correctly wired — tasks execute in order, events trigger subscribers. Run against `hatchet-lite` Docker container.

Keep E2E tests minimal — they verify wiring, not business logic.

## Naming Conventions

| Pattern | Purpose | Example |
|---------|---------|---------|
| `collect-{source}` | Cron collector (parent) | `collect-hackernews` |
| `collect-{source}-feed` | Per-feed child (fan-out) | `collect-rss-feed` |
| `process-{stage}` | Per-item processing (event-driven) | `process-webpage`, `process-transcription` |

## Adding a New Pipeline

1. Create `src/aggre/workflows/{name}.py`
2. Add a `register(h)` function that creates the workflow
3. Define workflow with `h.workflow(name=..., on_events=["item.new"], input_validator=ItemEvent, concurrency=...)`
4. Add self-filtering logic in the task (skip irrelevant items)
5. Extract business logic into a testable function (e.g. `process_one(engine, config, content_id)`)
6. Auto-discovered via `register(h)` function — no manual registration needed
7. Write unit tests for the business logic function
8. Add E2E test for workflow wiring
