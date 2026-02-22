# Migration Plan: Dagster + Medallion Architecture

## Goal

Migrate from CLI-based worker loops to Dagster orchestration, fully aligning with `docs/medallion-guidelines.md`. No backward compatibility needed — nuke and rebuild.

## Current State

- **Orchestration**: Click CLI + `run_loop()` with `time.sleep()` polling
- **Bronze**: `bronze_discussions` table in PostgreSQL (violates guideline: bronze = filesystem)
- **Silver**: `silver_content.raw_html` stores raw HTML in PostgreSQL (violates guideline: no raw data in silver)
- **Wrappers**: No bronze-aware wrappers — collectors call external APIs directly
- **Deployment**: Docker Compose with one container per pipeline stage

## Target State

- **Orchestration**: Dagster assets + sensors + schedules
- **Bronze**: Filesystem (`data/bronze/{source_type}/{external_id}/`) — immutable, directory-per-item
- **Silver**: PostgreSQL — transformed/normalized only, no raw blobs
- **Wrappers**: Bronze-aware wrappers for httpx, feedparser, yt-dlp, whisper
- **Deployment**: Dagster webserver + daemon + postgres

---

## Step 1: Add Dagster dependency and project skeleton

**What**: Install dagster, create package structure under `src/aggre/dagster_defs/`.

**Changes**:
- Add `dagster>=1.9`, `dagster-webserver>=1.9`, `dagster-postgres>=0.25` to `pyproject.toml`
- Create `src/aggre/dagster_defs/__init__.py` (Dagster definitions entry point)
- Create `src/aggre/dagster_defs/resources.py` (placeholder)
- Add `[tool.dagster]` section to `pyproject.toml` pointing to definitions module

**Success**: `uv sync` installs dagster. `uv run dagster definitions validate` passes.

---

## Step 2: Create bronze filesystem storage module

**What**: Core bronze read/write functions following medallion directory-per-item pattern.

**Changes**:
- Create `src/aggre/bronze.py` with:
  - `write_bronze(source_type, external_id, artifact_type, data, ext)` — write to `data/bronze/{source_type}/{external_id}/{artifact_type}.{ext}`
  - `read_bronze(source_type, external_id, artifact_type, ext)` — read from path
  - `bronze_exists(source_type, external_id, artifact_type, ext)` — check existence
  - `bronze_path(source_type, external_id, artifact_type, ext)` — return Path
  - `write_bronze_by_hash(source_type, url_hash, artifact_type, data, ext)` — for request-keyed storage
- Tests in `tests/test_bronze.py`

**Success**: Unit tests pass for read/write/exists operations with temp directories.

---

## Step 3: Create bronze-aware HTTP wrapper

**What**: Wrap httpx to check bronze before fetching, write bronze after fetching. Per medallion HTTP API client prescription.

**Changes**:
- Create `src/aggre/bronze_http.py` with:
  - Item-keyed wrapper: `fetch_item(source_type, external_id, url, client, bronze_root)` — check bronze, fetch on miss
  - Request-keyed wrapper: `fetch_url(source_type, url, client, bronze_root)` — check bronze by URL hash, fetch on miss
  - URL hash function for request-keyed storage
- Retry logic (tenacity) inside the wrapper
- Tests in `tests/test_bronze_http.py`

**Success**: Wrapper returns cached data on hit, fetches and caches on miss.

---

## Step 4: Refactor collectors to use bronze filesystem

**What**: Replace `_store_raw_item()` (PostgreSQL bronze) with filesystem bronze writes. Collectors write raw data to `data/bronze/`, then transform to silver.

**Changes per collector**:
- Each collector's `collect()`:
  1. Fetch from API (via bronze-aware wrapper when possible)
  2. Write raw response to `data/bronze/{source_type}/{external_id}/raw.json`
  3. Transform and upsert into `silver_discussions` (no bronze_discussion_id)
- Remove `_store_raw_item()` from `BaseCollector`
- Remove `bronze_discussion_id` parameter from `_upsert_discussion()`
- Add `bronze_root: Path` parameter to collectors
- Collectors affected: hackernews, reddit, rss, youtube, lobsters, huggingface, telegram

**Success**: Collectors write to filesystem bronze. Tests pass with mocked filesystem or temp dirs.

---

## Step 5: Refactor content fetcher to use bronze filesystem

**What**: Move raw HTML storage from `silver_content.raw_html` to `data/bronze/content/{url_hash}/response.html`. Content extraction reads from bronze.

**Changes**:
- `download_content()`: Write HTML to `data/bronze/content/{url_hash}/response.html` instead of `silver_content.raw_html`
- `extract_html_text()`: Read HTML from bronze filesystem instead of `silver_content.raw_html`
- Update state transitions: PENDING → FETCHED (directly, bronze stores the intermediate HTML)
- Remove `DOWNLOADED` from FetchStatus (bronze stores the raw, silver only tracks final state)

**Success**: Content download writes to filesystem. Extraction reads from filesystem. No raw_html in PostgreSQL.

---

## Step 6: Refactor transcriber to use bronze filesystem

**What**: Download audio to bronze path (keep permanently). Write full whisper JSON to bronze. Extract text to silver.

**Changes**:
- Download audio to `data/bronze/youtube/{video_id}/audio.opus` (never delete — medallion immutable)
- Write full whisper output to `data/bronze/youtube/{video_id}/whisper.json`
- Silver gets only: `body_text` = concatenated text, `detected_language` = language code
- Remove `finally: audio_path.unlink()` — bronze is immutable
- Cache check: if `whisper.json` exists in bronze, skip transcription

**Success**: Audio and whisper output persisted in bronze. Silver gets extracted text only.

---

## Step 7: Remove bronze_discussions table and clean up DB schema

**What**: Drop the PostgreSQL bronze table. Clean up silver schema. New Alembic migration.

**Changes**:
- Remove `BronzeDiscussion` class from `db.py`
- Remove `bronze_discussion_id` FK from `SilverDiscussion`
- Remove `raw_html` column from `SilverContent`
- Remove `DOWNLOADED` from `FetchStatus`
- Remove all bronze indexes from `db.py`
- Delete all existing Alembic migrations
- Create single new migration: `001_initial.py` with clean schema
- Update `conftest.py` to use new schema

**Success**: `alembic upgrade head` creates clean schema. Tests pass with new schema.

---

## Step 8: Create Dagster resources

**What**: Wrap external dependencies as Dagster ConfigurableResource classes.

**Changes in `src/aggre/dagster_defs/resources.py`**:
- `DatabaseResource(ConfigurableResource)` — wraps SQLAlchemy engine creation
- `AppConfigResource(ConfigurableResource)` — wraps YAML config loading
- `BronzeResource(ConfigurableResource)` — wraps bronze filesystem ops with configurable root

**Success**: Resources instantiate correctly with env vars.

---

## Step 9: Convert pipeline stages to Dagster ops and jobs

**What**: Each pipeline stage becomes a Dagster op within a job.

**Changes**:
- Create `src/aggre/dagster_defs/jobs/collect.py`:
  - `collect_op` — runs all collectors (or filtered)
  - `collect_job` — wraps the op
- Create `src/aggre/dagster_defs/jobs/content.py`:
  - `download_content_op` — fetch pending content URLs
  - `extract_content_op` — extract text from downloaded HTML
  - `content_job` — download then extract
- Create `src/aggre/dagster_defs/jobs/enrich.py`:
  - `enrich_op` — search HN/Lobsters for discussions
  - `enrich_job` — wraps the op
- Create `src/aggre/dagster_defs/jobs/transcribe.py`:
  - `transcribe_op` — download + transcribe YouTube videos
  - `transcribe_job` — wraps the op

**Success**: Jobs can be launched from Dagster UI and produce correct results.

---

## Step 10: Create Dagster sensors and schedules

**What**: Replace CLI `--loop` pattern with Dagster sensors/schedules.

**Changes in `src/aggre/dagster_defs/sensors.py`**:
- `collection_schedule` — hourly ScheduleDefinition triggering collect_job
- `content_sensor` — watches silver for `fetch_status=pending`, triggers content_job. Cursor = max content ID.
- `enrichment_sensor` — watches silver for `enriched_at IS NULL`, triggers enrich_job. Cursor = max content ID.
- `transcription_sensor` — watches silver for `transcription_status=pending`, triggers transcribe_job. Cursor = max content ID.

**Success**: Sensors detect new work and trigger jobs automatically.

---

## Step 11: Wire up Dagster Definitions

**What**: Single entry point combining all jobs, sensors, schedules, resources.

**Changes**:
- `src/aggre/dagster_defs/__init__.py`:
  - Import all jobs, sensors, schedules, resources
  - Create `defs = Definitions(jobs=..., sensors=..., schedules=..., resources=...)`
- Add `[tool.dagster]` to `pyproject.toml`: `module_name = "aggre.dagster_defs"`

**Success**: `dagster dev` shows all jobs and sensors in UI. `dagster definitions validate` passes.

---

## Step 12: Update CLI and remove worker infrastructure

**What**: Simplify CLI. Remove worker loop code.

**Changes**:
- Remove `worker.py` (worker_options, run_loop)
- Simplify `cli.py`: keep `status`, `telegram-auth`, `run-once`
- `run-once` calls same functions as Dagster ops (shared code)
- Remove individual worker commands (`download`, `extract-html-text`, `enrich-content-discussions`, `transcribe`, `collect` with --loop)

**Success**: `aggre run-once` and `aggre status` work. Makefile targets pass.

---

## Step 13: Rewrite tests

**What**: Update all tests for new architecture.

**Changes**:
- Update `conftest.py` for new schema (no bronze_discussions table)
- Update collector tests: verify bronze filesystem writes + silver DB writes
- Update content fetcher tests: verify bronze HTML storage
- Update transcriber tests: verify bronze audio/whisper storage
- Add tests for `bronze.py` module
- Add tests for `bronze_http.py` wrapper
- Add tests for Dagster jobs (direct function invocation)
- Remove tests for deleted code (worker loops, bronze_discussions)
- Update acceptance tests

**Success**: All tests pass. No references to deleted code.

---

## Step 14: Update Docker Compose for Dagster

**What**: Replace per-stage containers with Dagster deployment.

**Changes**:
- Create `dagster.yaml` for instance config (PostgreSQL for runs/events)
- Create `workspace.yaml` pointing to definitions module
- Update `docker-compose.yml`:
  - Keep `postgres` service
  - Keep `migrate` service
  - Replace collect/download/extract/enrich/transcribe with:
    - `dagster-webserver` — Dagster UI
    - `dagster-daemon` — runs sensors and schedules
- Update `Dockerfile` to include dagster

**Success**: `docker-compose up` starts Dagster with all pipelines running.

---

## Step 15: Final cleanup and documentation

**What**: Update docs, Makefile, verify everything.

**Changes**:
- Add `test` target to Makefile: `uv run pytest tests/`
- Update dev commands in CLAUDE.md
- Log all structural decisions to `DECISIONS.md`
- Verify `make test` and `make lint` pass
- Verify `dagster definitions validate` passes

**Success**: All CI checks pass. `DECISIONS.md` documents all architectural choices.

---

## Execution Order

Steps 1-7 form the core migration (bronze filesystem + schema cleanup).
Steps 8-11 add Dagster orchestration layer.
Steps 12-15 clean up and finalize.

Each step is committed independently after tests pass.
