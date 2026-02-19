# Design: One-Shot Local Execution (`run-once`)

## Problem

Running Aggre on a server requires proxies and continuous uptime. For local ad-hoc usage, we need a single command that runs the full pipeline once and exits. Re-runs should skip recently-fetched sources (per-source TTL) while downstream stages already skip completed work via status fields.

## Solution

Two new artifacts — existing code stays untouched:

1. **`aggre run-once` CLI command** — orchestrates all pipeline stages sequentially
2. **`docker-compose.local.yml`** — separate compose file for one-shot Docker execution

## CLI Command: `aggre run-once`

```
aggre run-once [--source-ttl=60] [--source=TYPE] [--skip-transcribe]
```

### Pipeline stages (sequential)

1. **Collect** — for each source, check `Source.last_fetched_at`. Skip if within `--source-ttl` minutes. Otherwise call the existing collector's `collect()`. Then fetch pending comments.
2. **Download** — call `download_content()` in a loop until no pending items remain.
3. **Extract text** — call `extract_html_text()` in a loop until no downloaded-but-unextracted items remain.
4. **Transcribe** (skippable) — call `transcribe()` in a loop until no pending transcriptions remain. Skip entirely with `--skip-transcribe`.
5. **Enrich** — call `enrich_content_discussions()` in a loop until no un-enriched content remains.

### Per-source TTL

Add a helper method to `BaseCollector`:

```python
def _should_skip_source(self, engine, source_id, ttl_minutes):
    """Check if source was fetched within ttl_minutes. Returns True to skip."""
```

Each collector's `collect()` gains an optional `source_ttl_minutes` parameter. When set, it checks `Source.last_fetched_at` per source entry and skips recent ones.

### Drain loops

Downstream stages (download, extract, transcribe, enrich) process in batches (default 50). The `run-once` command calls each in a `while` loop until the function returns 0 (no more pending items), draining the entire queue.

### Summary output

After all stages complete, print a summary to stdout:

```
=== Run Complete ===
Sources:  12 checked, 8 collected, 4 skipped (recent)
Discuss:  47 new, 120 updated
Content:  35 downloaded, 3 failed
Extract:  32 extracted, 2 failed
Transcr:  skipped
Enrich:   28 enriched
```

### Exit code

- 0: pipeline completed (individual item failures are logged but not fatal)
- 1: a stage crashed entirely (e.g., DB connection lost)

### Failure handling

No changes to existing failure logic. Items that fail stay in their failed state. Re-running the command processes new/pending items only. Failed items require manual intervention or a future retry mechanism.

## Docker Compose: `docker-compose.local.yml`

Separate file. Existing `docker-compose.yml` untouched.

```yaml
x-app-env: &app-env
  AGGRE_DATABASE_URL: postgresql+psycopg2://aggre:aggre@postgres/aggre

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: aggre
      POSTGRES_USER: aggre
      POSTGRES_PASSWORD: aggre
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aggre"]
      interval: 5s
      timeout: 5s
      retries: 5

  migrate:
    build: .
    command: uv run alembic upgrade head
    volumes:
      - ./data/app:/app/data
      - ./config.yaml:/app/config.yaml:ro
    environment:
      <<: *app-env
    env_file:
      - path: .env
        required: false
    depends_on:
      postgres:
        condition: service_healthy

  run-once:
    build: .
    command: uv run aggre run-once --source-ttl 60
    volumes:
      - ./data/app:/app/data
      - ./config.yaml:/app/config.yaml:ro
    environment:
      <<: *app-env
    env_file:
      - path: .env
        required: false
    depends_on:
      migrate:
        condition: service_completed_successfully
```

Usage:
```bash
docker compose -f docker-compose.local.yml up --build
```

Data persists in `./data/postgres/` across runs. The same DB volume is shared with the regular `docker-compose.yml` if both point to `./data/postgres/`.

## Files Changed

| File | Change |
|------|--------|
| `src/aggre/cli.py` | Add `run-once` command |
| `src/aggre/collectors/base.py` | Add `_should_skip_source()` helper |
| `docker-compose.local.yml` | New file |

## Out of Scope

- Automatic retry of failed items (existing failure handling unchanged)
- Modifying the existing `docker-compose.yml`
- Adding a scheduling/cron mechanism
- Proxy configuration (already supported via `AGGRE_PROXY_URL`)
