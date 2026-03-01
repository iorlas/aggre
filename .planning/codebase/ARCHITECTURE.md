# Architecture

**Analysis Date:** 2026-03-01

## Pattern Overview

**Overall:** Multi-stage content aggregation pipeline with Bronze/Silver medallion data model, orchestrated by Dagster.

```
  Schedule (hourly)          Sensors (30-60s poll)
       |                /       |        \        \
  collect_job     content  transcribe  enrich  comments
       |            job      job        job      job
       v            v        v          v        v
  [Collectors]  [Download→ [yt-dlp→  [Search  [Fetch HN/
   HN/Reddit/   Extract]   Whisper]   HN+Lob]  Reddit/Lob
   RSS/YT/etc]      |         |         |      comments]
       |            v         v         v        v
       v        SilverContent           SC    SilverObs
  SilverObs       .text       .text  .enriched  .comments_json
  + SilverContent .title   .detected_lang  _at
    (via ensure_content)

  Manual trigger: reprocess_job (rebuild silver from bronze)
```

**Key Characteristics:**
- Dagster-first orchestration: jobs, sensors, schedules, resource injection
- Framework-first architecture: business logic lives in dagster_defs ops/jobs, no separate pipeline layer
- Domain-aligned dagster_defs packages (collection, comments, content, enrichment, reprocess, transcription)
- Immutable raw data in bronze filesystem (JSON files)
- Parsed discussions (SilverObservation) with optional mutable field updates
- Content-independent entity (SilverContent) linked from multiple discussions
- Source-agnostic collector plugin architecture
- Null-check pattern for processing state (data presence, not status enums)

## Layers

**Orchestration (Dagster):**
- Purpose: Job scheduling, sensor-driven triggers, resource management
- Location: `src/aggre/dagster_defs/`
- Contains: Domain-aligned packages with jobs, sensors, schedules
- Resources: `DatabaseResource` (ConfigurableResource wrapping SQLAlchemy engine)
- Entry: `dagster dev` or `dagster definitions validate`

**Entry Point (CLI):**
- Purpose: Interactive commands not suited for Dagster (e.g., Telegram auth)
- Location: `src/aggre/cli.py`
- Contains: `telegram-auth` command only
- Used by: Human operators for one-time setup tasks

**Configuration:**
- Purpose: Load YAML config with env var overrides via pydantic-settings
- Location: `src/aggre/config.py`, `src/aggre/settings.py`
- Contains: Settings dataclass, source-specific config models (RssSource, RedditSource, etc.)
- Used by: All collectors, Dagster ops

**Data Models (ORM):**
- Purpose: SQLAlchemy ORM models and database schema
- Location: `src/aggre/db.py`
- Contains: Source, SilverObservation, SilverContent models, index definitions
- Used by: All layers writing to database

**Collectors (Plugin Layer):**
- Purpose: Source-specific API clients that fetch raw data and parse to SilverObservation
- Location: `src/aggre/collectors/`
- Contains: BaseCollector shared helpers, individual collectors (HackerNews, Reddit, RSS, YouTube, Lobsters, HuggingFace, Telegram)
- Depends on: db, config, urls, utils/http
- Used by: Dagster collection job, enrichment module

**Utilities:**
- Purpose: Generic reusable helpers with zero Aggre-specific logic
- Location: `src/aggre/utils/`
- Contains:
  - `bronze.py` — Immutable bronze filesystem writer (medallion pattern)
  - `bronze_http.py` — Bronze-aware HTTP wrapper with read-through cache
  - `db.py` — SQLAlchemy engine factory (`get_engine`) and UTC timestamp helper (`now_iso`)
  - `http.py` — Shared HTTP client factory with proxy + User-Agent support (context manager)
  - `logging.py` — Structured logging setup (structlog dual output: JSON file + console)
  - `urls.py` — Generic URL tools (`extract_domain`, `strip_tracking_params`)
- Pattern: Implements medallion-guidelines.md patterns; all functions are pure or side-effect-isolated

## Data Flow

**Collection Flow (Dagster collect_job):**

1. Dagster schedule triggers `collect_job` hourly
2. Each collector executes two methods sequentially:
   - `collect_references(config, settings, log)` → fetches API data, writes bronze `raw.json` per item, returns `list[ContentReference]`
   - `process_reference(raw_data, conn, source_id, log)` → normalizes one bronze reference into silver rows:
     - `ensure_content()` → SilverContent if URL exists
     - `_upsert_observation()` → SilverObservation (with optional updates to mutable fields)
     - For self-posts (Reddit selftext, Ask HN with text, Lobsters self-posts): creates SilverContent with `text` pre-populated
   - `_update_last_fetched()` on Source
3. The two-method split enables `reprocess_job` to call `process_reference()` alone from bronze

**Comment Fetch Flow (Dagster comments_job, triggered by comments_sensor):**

1. `comments_sensor` watches for SilverObservation where `comments_json IS NULL AND error IS NULL` for HN/Reddit/Lobsters
2. `comments_job` runs each collector's `collect_comments()`:
   - Fetches raw API comment response
   - Writes raw response to bronze (`{source_type}/{ext_id}/comments.json`)
   - Parses and stores in `SilverObservation.comments_json`

**Content Fetch Flow (Dagster content_job, triggered by content_sensor):**

1. `content_sensor` watches for SilverContent where `text IS NULL AND error IS NULL AND (domain NOT IN SKIP_DOMAINS OR domain IS NULL)` (SKIP_DOMAINS = youtube.com, youtu.be, m.youtube.com)
2. `content_job` runs:
   - Download phase: HTTP GET → store raw HTML in bronze filesystem → set `fetched_at`
   - Extract phase: trafilatura extraction → store `text` + `title` (queries `fetched_at IS NOT NULL AND text IS NULL AND error IS NULL`)
   - Failures set `error` with error message; skipped content sets `error = 'skipped:{reason}'`

**Transcription Flow (Dagster transcribe_job, triggered by transcription_sensor):**

1. `transcription_sensor` watches for SilverContent where `text IS NULL AND error IS NULL` joined to SilverObservation where `source_type = 'youtube'`
2. `transcribe_job` runs with whisper resilience (3-step check):
   - Step 1: If `bronze/youtube/{id}/whisper.json` exists → use cached transcription (no audio needed)
   - Step 2: If `bronze/youtube/{id}/audio.opus` exists → transcribe from cached audio (no download needed)
   - Step 3: Neither → download audio via yt-dlp, then transcribe
   - Success: store `text` (transcript) + `detected_language`
   - Failure: set `error` with error message

**Enrichment Flow (Dagster enrich_job, triggered by enrichment_sensor):**

1. `enrichment_sensor` watches for SilverContent where `text IS NOT NULL AND canonical_url IS NOT NULL AND enriched_at IS NULL`
2. `enrich_job` runs:
   - HackernewsCollector.search_by_url() → discover HN discussions
   - LobstersCollector.search_by_url() → discover Lobsters discussions
   - After both succeed: SilverContent.enriched_at = now()

**Reprocess Flow (Dagster reprocess_job, manual trigger):**

1. Triggered manually (no sensor/schedule)
2. Scans `data/bronze/{source_type}/*/raw.json` for all source types
3. For each ref file: instantiates appropriate collector, calls `process_reference()`
4. Rebuilds SilverContent + SilverObservation from bronze without touching external APIs
5. After reprocessing, content/transcription/comment sensors detect new work → trigger their jobs

## State Management

**Database Transactions:**
- Collectors use `engine.begin()` for atomic writes
- Dagster ops use `engine.begin()` for state transitions
- Reads use `engine.connect()` (read-only)
- Race conditions handled via PostgreSQL upsert (ON CONFLICT DO NOTHING/UPDATE)

**Processing State (null-check pattern):**

Three independent processing flows tracked via data presence, not status enums:

1. **Content (article/paper):** `text IS NULL AND error IS NULL` → needs processing; `text IS NOT NULL` → done; `error IS NOT NULL` → failed/skipped
2. **Transcription (video):** Same pattern, routed by join to SilverObservation where `source_type = 'youtube'`
3. **Comments (discussion threads):** `comments_json IS NULL AND error IS NULL` → needs fetching; `comments_json IS NOT NULL` → done

## Key Abstractions

**BaseCollector:**
- Location: `src/aggre/collectors/base.py`
- Methods: `_ensure_source()`, `_write_bronze()`, `_upsert_observation()`, `_ensure_self_post_content()`, `_update_last_fetched()`, `_query_pending_comments()`, `_mark_comments_done()`, `_is_source_recent()`, `_get_fetch_limit()`, `_is_initialized()`

**Collector Protocol (two-method split):**
- Location: `src/aggre/collectors/base.py`
- `collect_references(config, settings, log) -> list[ContentReference]` — fetch feed, write bronze, return references (no DB access)
- `process_reference(raw_data, conn, source_id, log) -> None` — normalize one bronze reference into silver rows
- `collect_comments(engine, config, settings, log) -> int` — optional source-specific method; fetches and stores comment threads (only HN, Reddit, Lobsters implement it)
- SearchableCollector extends: `def search_by_url(url, engine, config, settings, log) -> int`
- The split enables `reprocess_job` to rebuild silver from bronze without hitting APIs

**DatabaseResource:**
- Location: `src/aggre/dagster_defs/resources.py`
- Pattern: `dg.ConfigurableResource` wrapping SQLAlchemy engine creation
- Used by: All sensors via parameter injection

**SilverContent Factory (ensure_content):**
- Location: `src/aggre/urls.py`
- Pattern: Normalize URL → find or create SilverContent → return id
- Handles: Race conditions via ON CONFLICT DO NOTHING + retry

**State Transition Helpers:**
- Locations: `dagster_defs/content/job.py`, `dagster_defs/transcription/job.py`
- Pattern: Helper functions update SilverContent columns (`text`, `error`, `fetched_at`, `detected_language`) via `engine.begin()` + `sa.update()`
- Content job: `_mark_downloaded()` sets `fetched_at`; `_mark_extracted()` sets `text` + `title`; `_mark_failed()` sets `error` + `fetched_at`; `_mark_skipped()` sets `error='skipped:{reason}'` + `fetched_at`; `_mark_extract_failed()` sets `error` + `fetched_at`
- Transcription job: `_mark_transcribed()` sets `text` + `detected_language`; `_mark_transcription_failed()` sets `error`

## Formal Verification

Pipeline concurrency invariants (sensor exclusion, state transitions, null-check correctness) are modeled as TLA+ specifications. See `docs/guidelines/formal-verification.md` for the spec-first workflow and verification instructions.

## Error Handling

**Strategy:** Batch-tolerant with per-item exception handling

**Patterns:**

1. **Collector errors:** Individual API failures logged but don't stop batch
2. **Database constraint violations:** Silent via ON CONFLICT DO NOTHING (expected duplicates)
3. **Processing errors:** `error` column set with error message for later inspection (null-check pattern)
4. **Dagster:** Op-level retries and failure handling via Dagster framework

## Cross-Cutting Concerns

**Logging:**
- Implementation: structlog with dual output (JSON file + console)
- Location: `src/aggre/utils/logging.py`
- Pattern: All modules receive BoundLogger, use log.info/exception with dot-notation events

**Authentication:**
- Reddit: PRAW with credentials from env vars (AGGRE_REDDIT_*)
- Telegram: async TelegramClient with session string (AGGRE_TELEGRAM_SESSION)
- YouTube: yt-dlp with optional proxy via config.settings.proxy_url
- Other sources: Public APIs or no auth needed

**Rate Limiting:**
- Config: `reddit_rate_limit`, `hn_rate_limit`, `lobsters_rate_limit`, `telegram_rate_limit` in Settings
- Pattern: Applied per-source in collector loop

**Idempotency:**
- Duplicates: SilverObservation dedup via (source_type, external_id) unique constraint
- Score/title updates: SilverObservation upserts mutable fields on re-insert
- Content enrichment: Tracked via enriched_at flag to avoid re-searching

---

*Architecture analysis: 2026-03-01*
