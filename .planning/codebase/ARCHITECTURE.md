# Architecture

**Analysis Date:** 2026-02-22

## Pattern Overview

**Overall:** Multi-stage content aggregation pipeline with Bronze/Silver medallion data model, orchestrated by Dagster.

**Key Characteristics:**
- Dagster-first orchestration: jobs, sensors, schedules, resource injection
- Domain-aligned dagster_defs packages (collection, content, enrichment, transcription)
- Immutable raw data in bronze filesystem (JSON files)
- Parsed discussions (SilverDiscussion) with optional mutable field updates
- Content-independent entity (SilverContent) linked from multiple discussions
- Source-agnostic collector plugin architecture
- Status-driven state machines for content fetch/transcription/enrichment

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
- Used by: All collectors, content pipeline, Dagster ops

**Data Models (ORM):**
- Purpose: SQLAlchemy ORM models and database schema
- Location: `src/aggre/db.py`
- Contains: Source, SilverDiscussion, SilverContent models, index definitions
- Used by: All layers writing to database

**Collectors (Plugin Layer):**
- Purpose: Source-specific API clients that fetch raw data and parse to SilverDiscussion
- Location: `src/aggre/collectors/`
- Contains: BaseCollector shared helpers, individual collectors (HackerNews, Reddit, RSS, YouTube, Lobsters, HuggingFace, Telegram)
- Depends on: db, config, urls, utils/http, status enums
- Used by: Dagster collection job, enrichment module

**Content Pipeline:**
- Purpose: Download and extract text from article URLs
- Location: `src/aggre/content_downloader.py` (HTTP download → bronze), `src/aggre/content_extractor.py` (bronze → silver text)
- State transitions: PENDING → DOWNLOADED → FETCHED/FAILED/SKIPPED
- Used by: Dagster content job

**Transcription Pipeline:**
- Purpose: Download YouTube videos and transcribe to text
- Location: `src/aggre/transcriber.py`
- State transitions: PENDING → DOWNLOADING → TRANSCRIBING → COMPLETED/FAILED
- Used by: Dagster transcription job

**Enrichment Pipeline:**
- Purpose: Search HN/Lobsters for discussions about content URLs
- Location: `src/aggre/enrichment.py`
- Used by: Dagster enrichment job

**Utilities:**
- Purpose: Generic reusable helpers with zero Aggre-specific logic
- Location: `src/aggre/utils/`
- Contains: Bronze filesystem writer, bronze-aware HTTP wrapper, shared HTTP client, structured logging
- Pattern: Implements medallion-guidelines.md patterns

## Data Flow

**Collection Flow (Dagster collect_job):**

1. Dagster schedule triggers `collect_job` hourly
2. Each collector op executes:
   - Calls `_ensure_source()` to register/retrieve Source record
   - Fetches API data (via HTTP)
   - For each item:
     - Writes bronze JSON to filesystem via `_write_bronze()`
     - `_upsert_discussion()` → SilverDiscussion (with optional updates to mutable fields)
     - `ensure_content()` → SilverContent if URL exists in item
   - Calls `_update_last_fetched()` on Source
   - Returns count of new discussions
3. For Reddit/HN/Lobsters: separate comment collection ops

**Content Fetch Flow (Dagster content_job, triggered by content_sensor):**

1. `content_sensor` watches for SilverContent with fetch_status=PENDING
2. `content_job` runs:
   - Download phase: HTTP GET → store raw HTML in bronze filesystem (PENDING → DOWNLOADED)
   - Extract phase: trafilatura extraction → store body_text + title (DOWNLOADED → FETCHED)
   - Failures transition to FAILED with error message

**Transcription Flow (Dagster transcribe_job, triggered by transcription_sensor):**

1. `transcription_sensor` watches for SilverContent with transcription_status=PENDING
2. `transcribe_job` runs:
   - PENDING → DOWNLOADING: download video via yt-dlp
   - DOWNLOADING → TRANSCRIBING: transcribe via faster-whisper
   - TRANSCRIBING → COMPLETED: store body_text (transcript)

**Enrichment Flow (Dagster enrich_job, triggered by enrichment_sensor):**

1. `enrichment_sensor` watches for SilverContent where enriched_at IS NULL
2. `enrich_job` runs:
   - HackernewsCollector.search_by_url() → discover HN discussions
   - LobstersCollector.search_by_url() → discover Lobsters discussions
   - After both succeed: SilverContent.enriched_at = now()

## State Management

**Database Transactions:**
- Collectors use `engine.begin()` for atomic writes
- Content pipeline uses `engine.begin()` for state transitions
- Reads use `engine.connect()` (read-only)
- Race conditions handled via PostgreSQL upsert (ON CONFLICT DO NOTHING/UPDATE)

**Status Lifecycles:**

Three independent state machines run in parallel:

1. **FetchStatus (article/paper content):** PENDING → (DOWNLOADED → FETCHED | SKIPPED | FAILED)
2. **TranscriptionStatus (video content):** PENDING → DOWNLOADING → TRANSCRIBING → (COMPLETED | FAILED)
3. **CommentsStatus (discussion threads):** PENDING → DONE

## Key Abstractions

**BaseCollector:**
- Location: `src/aggre/collectors/base.py`
- Methods: `_ensure_source()`, `_write_bronze()`, `_upsert_discussion()`, `_update_last_fetched()`, `_query_pending_comments()`, `_mark_comments_done()`, `_is_source_recent()`, `_get_fetch_limit()`, `_is_initialized()`

**Collector Protocol:**
- Location: `src/aggre/collectors/base.py`
- Signature: `def collect(engine, config, settings, log) -> int`
- SearchableCollector extends: `def search_by_url(url, engine, config, settings, log) -> int`

**DatabaseResource:**
- Location: `src/aggre/dagster_defs/resources.py`
- Pattern: `dg.ConfigurableResource` wrapping SQLAlchemy engine creation
- Used by: All sensors via parameter injection

**SilverContent Factory (ensure_content):**
- Location: `src/aggre/urls.py`
- Pattern: Normalize URL → find or create SilverContent → return id
- Handles: Race conditions via ON CONFLICT DO NOTHING + retry

**State Transition Functions:**
- Locations: `content_downloader.py`, `content_extractor.py`, `transcriber.py`
- Examples: `content_downloaded()`, `content_fetched()`, `transcription_completed()`
- Pattern: Each function calls `update_content()` with new status + metadata

## Error Handling

**Strategy:** Batch-tolerant with per-item exception handling

**Patterns:**

1. **Collector errors:** Individual API failures logged but don't stop batch
2. **Database constraint violations:** Silent via ON CONFLICT DO NOTHING (expected duplicates)
3. **Pipeline errors:** State transitions to FAILED with error message for later inspection
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
- Duplicates: SilverDiscussion dedup via (source_type, external_id) unique constraint
- Score/title updates: SilverDiscussion upserts mutable fields on re-insert
- Content enrichment: Tracked via enriched_at flag to avoid re-searching

---

*Architecture analysis: 2026-02-22*
