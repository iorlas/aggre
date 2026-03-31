# Hatchet Migration Plan

**Date:** 2026-03-07
**Branch:** `feat/hatchet-migration`
**Tag:** `dagster-last` preserves last working Dagster state on `dev`

## Context

Aggre uses Dagster but the abstraction is wrong — Dagster orchestrates **jobs** (asset-centric), Aggre needs to orchestrate **items** (task-centric). Research 030 evaluated 7 tools and selected Hatchet.

**Key finding:** Business logic is fully decoupled from Dagster. Collectors, download, extract, transcribe, search — all pure functions wrapped in `@op` decorators. Tests call raw functions directly. Migration = replace the orchestration wrapper, not rewrite business logic.

## What Gets Deleted

- `src/aggre/dagster_defs/` — entire directory (20+ files)
- `dagster.yaml`, `dagster.prod.yaml`, `workspace.yaml`
- Dependencies: `dagster`, `dagster-webserver`, `dagster-postgres`
- Docker services: `dagster-webserver`, `dagster-daemon`, `dagster-postgres`
- `Makefile` target: `validate` (dagster definitions validate)

## What Stays (Zero Changes)

- `src/aggre/collectors/` — pure business logic, no Dagster imports
- `src/aggre/utils/` — bronze, bronze_http, http, urls, db
- `src/aggre/db.py` — ORM models
- `src/aggre/config.py`, `settings.py` — configuration
- `src/aggre/tracking/` — StageTracking (database-level, framework-independent)
- `alembic/` — migrations
- Most tests — they call raw functions, not Dagster ops

## What Gets Created

### New dependency

`hatchet-sdk>=1.4` in `pyproject.toml`

### New structure: `src/aggre/workflows/`

```
src/aggre/workflows/
├── __init__.py          # Hatchet client + worker setup
├── collection.py        # Cron-triggered collection (all sources)
├── webpage.py           # download -> extract workflow
├── transcription.py     # transcribe workflow
├── comments.py          # fetch comments workflow
├── discussion_search.py # search HN+Lobsters workflow
└── reprocess.py         # manual trigger workflow
```

### Pattern per workflow

```python
from aggre.workflows import hatchet

wf = hatchet.workflow(name="webpage-pipeline")

@wf.task()
async def download(input):
    # Business logic from current webpage/job.py download_webpage_op
    ...

@wf.task(parents=[download])
async def extract(input):
    # Business logic from current webpage/job.py extract_webpage_op
    ...
```

### Docker changes

- Remove: `dagster-webserver`, `dagster-daemon`, `dagster-postgres` services
- Add: `hatchet-engine` (single container, Postgres-only mode)
- Add: `hatchet-worker` (runs the Aggre workflows)
- Net result: fewer containers than before

### Resource injection replacement

| Current (Dagster) | New (Hatchet) |
|---|---|
| `DatabaseResource(ConfigurableResource)` via context | Direct `get_engine()` call |
| `AppConfigResource(ConfigurableResource)` via context | Direct `load_config()` call |

## Migration Order

1. **Setup**: Add hatchet-sdk, create `workflows/__init__.py` with client setup
2. **Collection**: Port collection jobs (simplest — just cron + call collector)
3. **Webpage**: Port download + extract (DAG workflow, validates the pattern)
4. **Transcription**: Port transcribe (similar pattern to webpage)
5. **Comments**: Port comment fetching
6. **Discussion Search**: Port search workflow
7. **Reprocess**: Port manual trigger
8. **Cleanup**: Delete `dagster_defs/`, remove dagster deps, update Docker
9. **Tests**: Update test fixtures, add E2E workflow tests
10. **Docs**: Update CLAUDE.md, architecture docs, guidelines

## Success Criteria

- `make test` passes with 95%+ coverage
- `make lint` passes
- Hatchet worker starts and connects to engine
- Collection workflows trigger on cron
- Processing workflows trigger on events
- No Dagster imports remain in codebase
- Less total code than Dagster equivalent

## Rollback

If migration fails: `git checkout dev` — `dagster-last` tag preserves the last working state.
