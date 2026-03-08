# S3/Garage Remote Performance — Review Findings

> **Note:** Path references below use pre-migration `dagster_defs/` paths. Business logic now lives in `src/aggre/workflows/`.

## Critical (Must Fix Before Remote Deployment)

- [x] **C1: Reprocess job broken on S3** — `src/aggre/dagster_defs/reprocess/job.py` uses `Path.glob()` to scan bronze directory. Completely non-functional with S3 backend (silently returns 0). Fix: add `list_keys(prefix)` to `BronzeStore`, implement with `list_objects_v2` for S3, rewrite reprocess to use it.

- [x] **C2: Transcription audio not persisted to S3** — `src/aggre/dagster_defs/transcription/job.py:83-127`. Audio downloaded by yt_dlp only saved to local temp dir, never uploaded to S3. Lost on cleanup, must re-download from YouTube. Fix: add `write_bytes()`/`read_bytes()` to `BronzeStore`, upload audio after download, check S3 before re-downloading.

## Important (Should Fix Soon)

- [x] **I1: Double round-trip in bronze_http.py** — `src/aggre/utils/bronze_http.py:62-63`. `bronze_exists()` (HEAD) + `read_bronze()` (GET) = 2 requests per cache hit. Fix: add `read_or_none()` to `BronzeStore`.

- [x] **I2: Double round-trip in transcription cache** — `src/aggre/dagster_defs/transcription/job.py:74-77`. Same exists+read pattern as I1. Fix: use `read_or_none()`.

- [x] **I3: No boto3 timeouts/retries** — `src/aggre/utils/bronze.py:61-73`. Default boto3 config, no timeouts. A hung Garage connection blocks thread indefinitely. Fix: add `botocore.config.Config` with `connect_timeout=5`, `read_timeout=30`, `retries={"max_attempts": 3, "mode": "adaptive"}`, `max_pool_connections=20`.

- [x] **I4: Thread-unsafe get_store() singleton** — `src/aggre/utils/bronze.py:106-125`. Check-then-act without lock. Concurrent threads in webpage pipeline can race. Fix: `threading.Lock` with double-checked locking.

- [ ] **I5: Redundant bronze writes every collection run** — All collectors via `BaseCollector._write_bronze()` in `src/aggre/collectors/base.py:79-81`. Every item written to S3 every run, even if unchanged. ~4,320 unnecessary PUTs/day for HN alone. Fix: only write for genuinely new items (use DB upsert result).

## Minor (Nice to Have)

- [x] **M1: BronzeStore protocol text-only** — `src/aggre/utils/bronze.py:20-32`. `read()`/`write()` only support `str`. Audio is binary. Fix: add `read_bytes()`/`write_bytes()`.

- [ ] **M2: `bronze_path()` misleading for S3** — `src/aggre/utils/bronze.py:160-182`. Returns local path that doesn't exist on S3 backend. Fix: deprecate or assert on S3 backend.

- [ ] **M3: No S3 connection health check at startup** — If Tailscale/Garage down, first S3 op fails with opaque error. Fix: add health check in Dagster resource init.

## Hatchet Smoke Test Issues (2026-03-07)

- [x] **H1: Browserless 400 errors on all downloads** — All 50 webpage download attempts fail with `Client error '400 Bad Request'` from `http://browserless:3000/function`. Error includes "Navigation timeout of 55000 ms exceeded". Possibly API change in Browserless version or configuration issue. Investigate and fix.

- [x] **H2: RSS collector hangs on slow feeds** — RSS collection with 27 feeds takes 15+ minutes. Some feeds appear to hang indefinitely (no HTTP timeout in feedparser/requests). Add HTTP timeouts (e.g., 30s per feed) to prevent single slow feed from blocking the entire collection.

- [x] **H3: Pydantic serialization warnings in Hatchet tasks** — Tasks returning dict results trigger `PydanticSerializationUnexpectedValue(Expected EmptyModel)`. Cosmetic but noisy. Fix: either define proper input/output Pydantic models for each task, or suppress the warning.

- [ ] **H4: Hatchet token lifecycle** — Token is manually generated and stored in `.env`. Expires after ~3 months (JWT exp). No automated rotation. Document the token generation process and consider automating it in `make dev-remote` setup.

- [x] **H5: No application logging visible in Hatchet worker** — Python `logging` output from business logic doesn't appear in `docker compose logs`. Only Hatchet SDK `[INFO]` messages visible. Need to configure logging handler to output to stdout/stderr.

## Event-Driven Migration (2026-03-08)

- [ ] **E1: Router optimization** — If skip runs become noisy in Hatchet UI (e.g. `process-transcription` skipping non-YouTube items), add a router workflow that dispatches to the correct downstream workflow instead of broadcasting `item.new` to all subscribers.

- [ ] **E2: Hatchet data retention** — Verify/configure retention on Hatchet server. Old workflow runs accumulate in Postgres. Check `HATCHET_RETENTION_PERIOD` env var or equivalent.

- [ ] **E3: Remove StageTracking** — `src/aggre/tracking/` module is no longer used by workflows but kept for Grafana dashboards. After dashboards are migrated to Hatchet OLAP tables, remove the module, DB table, and related alembic migration. **Pre-requisites:** (a) set `SilverContent.enriched_at` in `search_one()` so discussion search coverage is queryable without StageTracking; (b) catch final-retry failures in Hatchet task wrappers and write `SilverContent.error` / `SilverDiscussion.error` so permanently-failed items don't stay in "pending" (`text IS NULL AND error IS NULL`) state; (c) migrate Grafana dashboards to Hatchet OLAP tables.

- [ ] **E4: Backfill CLI** — Need a way to trigger per-item workflows for existing unprocessed content (replaces old batch functions). E.g. `python -m aggre.backfill webpage` queries DB for unprocessed content and emits `item.new` events.

- [ ] **E5: Comment events for discussions without content** — `_emit_item_event` skips discussions without `content_id` (e.g. Ask HN, Telegram messages). These items never get comment-fetching via the event-driven path. Either emit a separate event for comment-only items or keep a lightweight cron-based comments fallback.

- [ ] **E6: `_COMMENT_SOURCES` hardcoded** — If a new collector adds `fetch_discussion_comments()`, the tuple in `comments.py:24` must be manually updated. Consider a dynamic check or a test that verifies all collectors with that method are listed.

- [ ] **E7: DB query failure path in `_emit_item_event` untested** — The `try/except` at `collection.py:93` catches both DB query and `hatchet.event.push` failures. Only the push failure path has a dedicated test. Same except clause, so functionally covered, but no explicit DB-failure test.
