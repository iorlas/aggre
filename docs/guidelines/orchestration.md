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

## Architecture Pattern: Hybrid DAG + Events

### Within a Pipeline: Explicit DAG

Steps within a single pipeline are connected via explicit parent-child relationships. Clear dependencies, easy debugging.

```python
wf = hatchet.workflow(name="webpage-pipeline")

@wf.task()
async def download(input):
    # Fetch webpage content
    ...

@wf.task(parents=[download])
async def extract(input):
    # Extract text from HTML
    ...
```

### Between Pipelines: Domain Events

Pipelines communicate via domain events. Extensible fan-out — adding a new pipeline means subscribing to an existing event.

```
collect (cron) --> emits "content.new" per item
  |
content pipeline (DAG): download --> extract --> [emits "content.ready"]
  |
embed pipeline (subscribes to "content.ready")
similarity pipeline (subscribes to "content.ready")
categorize pipeline (subscribes to "content.ready")
```

### Rules

1. **Within a pipeline**: explicit DAG. Clear dependencies, easy debugging.
2. **Between pipelines**: domain events. Extensible fan-out.
3. **Normal flow**: events trigger all subscribers automatically.
4. **Partial reruns**: trigger specific workflows directly — don't re-emit events.
5. **New pipeline catch-up**: replay events for existing items (one-time backfill script).

## Workflow Patterns

### Cron Collection

Each source has a collection workflow triggered on cron schedule:

```python
@hatchet.workflow(name="collect-hackernews", on_crons=["0 */2 * * *"])
```

The collection workflow calls the pure collector function, then emits `content.new` events for each new item.

### Event-Driven Processing

Processing pipelines subscribe to events:

```python
@hatchet.workflow(name="webpage-pipeline", on_events=["content.new"])
```

### Partial Reruns

To reprocess specific items without re-triggering the full pipeline:

```python
# Rerun only extraction for specific items
for item_id in items_to_reprocess:
    await hatchet.workflows["webpage-pipeline"].run({"id": item_id})
```

### Resource Injection

No framework-specific resource wrappers. Direct function calls:

```python
# Instead of Dagster's ConfigurableResource + context.resources
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
def download_webpage(url: str, engine: Engine) -> ...:
    ...

# Test calls the function directly
def test_download_webpage(db_engine):
    result = download_webpage("https://example.com", db_engine)
    assert result.status == "ok"
```

### E2E Tests (workflow wiring)

Verify that workflows are correctly wired — tasks execute in order, events trigger subscribers. Run against `hatchet-lite` Docker container.

Keep E2E tests minimal — they verify wiring, not business logic.

## Adding a New Pipeline

1. Create `src/aggre/workflows/{name}.py`
2. Define workflow with `hatchet.workflow()`
3. Add tasks with `@wf.task()` and parent dependencies
4. Subscribe to relevant events (or add cron trigger)
5. Register in `src/aggre/workflows/__init__.py`
6. Write unit tests for business logic functions
7. Add E2E test for workflow wiring
