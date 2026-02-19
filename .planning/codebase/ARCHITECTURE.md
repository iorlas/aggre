# Architecture

**Analysis Date:** 2026-02-20

## Pattern Overview

**Overall:** Multi-stage content aggregation pipeline with Bronze/Silver medallion data model

**Key Characteristics:**
- Immutable raw data (BronzeDiscussion) never updated after creation
- Parsed discussions (SilverDiscussion) with optional mutable field updates
- Content-independent entity (SilverContent) linked from multiple discussions
- Source-agnostic collector plugin architecture
- Status-driven state machines for content fetch/transcription/enrichment
- Concurrent worker loop with configurable batch processing and intervals

## Layers

**Entry Point (CLI):**
- Purpose: Command-line interface for all operations
- Location: `src/aggre/cli.py`
- Contains: Click command definitions, worker loop orchestration
- Depends on: config, db, collectors, content_fetcher, enrichment, transcriber, logging
- Used by: Human operators, orchestration scripts

**Configuration:**
- Purpose: Load YAML config with env var overrides via pydantic-settings
- Location: `src/aggre/config.py`
- Contains: Settings dataclass, source-specific config models (RssSource, RedditSource, etc.)
- Depends on: yaml, pydantic
- Used by: All collectors, content pipeline, CLI

**Data Models (ORM):**
- Purpose: SQLAlchemy ORM models and database schema
- Location: `src/aggre/db.py`
- Contains: Source, BronzeDiscussion, SilverDiscussion, SilverContent models, index definitions
- Depends on: sqlalchemy
- Used by: All layers writing to database

**Collectors (Plugin Layer):**
- Purpose: Source-specific API clients that fetch raw data and parse to SilverDiscussion
- Location: `src/aggre/collectors/`
- Contains: BaseCollector shared helpers, individual collectors (HackerNews, Reddit, RSS, YouTube, Lobsters, HuggingFace, Telegram)
- Depends on: db, config, urls, http, status enums
- Used by: CLI collect command, enrichment module

**URL Management:**
- Purpose: Normalize URLs and manage SilverContent entity lifecycle
- Location: `src/aggre/urls.py`
- Contains: URL normalization (domain-specific rules), domain extraction, ensure_content() factory
- Depends on: db
- Used by: All collectors, content_fetcher, enrichment

**HTTP Client:**
- Purpose: Shared HTTP client factory with browser User-Agent and proxy support
- Location: `src/aggre/http.py`
- Contains: create_http_client() with default headers and timeout
- Depends on: httpx
- Used by: Collectors requiring HTTP requests

**Content Pipeline:**
- Purpose: Download and extract text from article URLs
- Location: `src/aggre/content_fetcher.py`
- Contains: download_content(), extract_html_text(), content state transitions (PENDING → DOWNLOADED → FETCHED/FAILED/SKIPPED)
- Depends on: db, http, trafilatura, config, status enums
- Used by: CLI download command, extract-html-text command

**Transcription Pipeline:**
- Purpose: Download YouTube videos and transcribe to text
- Location: `src/aggre/transcriber.py`
- Contains: transcribe(), yt-dlp video download, faster-whisper transcription, transcription state transitions
- Depends on: db, config, yt_dlp, faster_whisper, status enums
- Used by: CLI transcribe command

**Enrichment Pipeline:**
- Purpose: Search HN/Lobsters for discussions about content URLs
- Location: `src/aggre/enrichment.py`
- Contains: enrich_content_discussions(), calls SearchableCollector.search_by_url()
- Depends on: db, config, collectors (HN, Lobsters), urls
- Used by: CLI enrich-content-discussions command

**Status Enums:**
- Purpose: Define lifecycle states for FetchStatus, TranscriptionStatus, CommentsStatus
- Location: `src/aggre/statuses.py`
- Contains: StrEnum definitions for state machines
- Depends on: enum
- Used by: All pipeline modules, db schema

**Worker Utilities:**
- Purpose: Reusable loop and CLI decorator helpers
- Location: `src/aggre/worker.py`
- Contains: worker_options() decorator, run_loop() with sleep/retry
- Depends on: click, structlog
- Used by: CLI commands

**Logging:**
- Purpose: Structured logging with dual output (JSON to file, human to stdout)
- Location: `src/aggre/logging.py`
- Contains: setup_logging() with structlog/stdlib configuration
- Depends on: structlog, logging
- Used by: All modules (via BoundLogger passed from CLI)

## Data Flow

**Collection Flow:**

1. CLI invokes `collect_cmd()` with optional source_type filter
2. Initializes all collectors (or filtered subset) in ThreadPoolExecutor
3. Each collector executes:
   - Calls `_ensure_source()` to register/retrieve Source record
   - Fetches API data (via HTTP)
   - For each item:
     - `_store_raw_item()` → BronzeDiscussion (immutable)
     - `_upsert_discussion()` → SilverDiscussion (with optional updates to mutable fields)
     - `ensure_content()` → SilverContent if URL exists in item
   - Calls `_update_last_fetched()` on Source
   - Returns count of new discussions
4. For Reddit/HN/Lobsters: separate `collect_comments()` fetches threaded comments into comments_json
5. Logs event with dot notation (e.g., `hackernews.discussions_stored`)

**Content Fetch Flow:**

1. CLI invokes `download_cmd()` with batch size and worker count
2. Query SilverContent where fetch_status=PENDING, limit to batch
3. For each content:
   - Skip YouTube/PDF domains
   - HTTP GET with browser User-Agent via create_http_client()
   - Store raw_html in SilverContent (PENDING → DOWNLOADED)
   - Transition to FetchStatus.FAILED on exception
4. Extract HTML text phase:
   - Query SilverContent where fetch_status=DOWNLOADED
   - Use trafilatura to extract body_text and title
   - Transition to FetchStatus.FETCHED (or FAILED)

**Transcription Flow:**

1. CLI invokes `transcribe()` with batch size
2. Query SilverContent WHERE transcription_status IN (PENDING, DOWNLOADING, TRANSCRIBING)
3. For each pending video:
   - Get YouTube video_id from linked SilverDiscussion.external_id
   - PENDING → DOWNLOADING: download video via yt_dlp to temp dir
   - DOWNLOADING → TRANSCRIBING: transcribe via faster-whisper with caching
   - TRANSCRIBING → COMPLETED: store body_text (transcript), detected_language
   - Any error → FAILED with error message

**Enrichment Flow:**

1. CLI invokes `enrich_content_discussions()` with batch size
2. Query SilverContent where enriched_at IS NULL (never enriched)
3. For each content URL:
   - HackernewsCollector.search_by_url() → discover HN discussions
   - LobstersCollector.search_by_url() → discover Lobsters discussions
   - Each creates new SilverDiscussion rows if found
   - After both succeed: SilverContent.enriched_at = now()
4. Returns aggregate counts per platform

## State Management

**Database Transactions:**

- Collectors use `engine.begin()` for atomic writes (BronzeDiscussion + SilverDiscussion in one txn)
- Content pipeline uses `engine.begin()` for state transitions
- Reads use `engine.connect()` (read-only)
- Race conditions handled via PostgreSQL upsert (ON CONFLICT DO NOTHING/UPDATE)

**Status Lifecycles:**

Three independent state machines run in parallel:

1. **FetchStatus (article/paper content):** PENDING → (DOWNLOADED → FETCHED | SKIPPED | FAILED)
2. **TranscriptionStatus (video content):** PENDING → DOWNLOADING → TRANSCRIBING → (COMPLETED | FAILED)
3. **CommentsStatus (discussion threads):** PENDING → DONE (independent of fetch/transcription)

Each state machine tracks progress asynchronously. A single SilverContent can be simultaneously:
- Fetch: FETCHED (article extracted)
- Transcription: COMPLETED (video transcribed)
- Enrichment: enriched_at set

## Key Abstractions

**BaseCollector:**
- Purpose: Shared methods for all collectors
- Location: `src/aggre/collectors/base.py`
- Methods: `_ensure_source()`, `_store_raw_item()`, `_upsert_discussion()`, `_update_last_fetched()`, `_query_pending_comments()`, `_mark_comments_done()`
- Pattern: All collector subclasses inherit and call these helpers

**Collector Protocol:**
- Purpose: Define interface for collector implementations
- Location: `src/aggre/collectors/base.py`
- Signature: `def collect(engine, config, log) -> int`
- SearchableCollector extends: `def search_by_url(url, engine, config, log) -> int`

**SilverContent Factory (ensure_content):**
- Purpose: Idempotent content creation with deduplication
- Location: `src/aggre/urls.py`
- Pattern: Normalize URL → find or create SilverContent → return id
- Handles: Race conditions via ON CONFLICT DO NOTHING + retry

**State Transition Functions:**
- Purpose: Semantic state machine moves with logging
- Locations: `content_fetcher.py`, `transcriber.py`
- Examples: `content_downloaded()`, `transcription_completed()`, etc.
- Pattern: Each function calls `_update_content()` with new status + metadata

**URL Normalization:**
- Purpose: Deduplicate content across sources
- Location: `src/aggre/urls.py`
- Rules: Domain-specific (arxiv, YouTube, GitHub, Reddit, HN, Medium) + generic tracking param removal
- Result: Canonical URLs enable cross-source content linking

## Entry Points

**CLI (Main):**
- Location: `src/aggre/cli.py`
- Triggers: `aggre collect|download|extract-html-text|enrich-content-discussions|transcribe|backfill|status`
- Responsibilities:
  - Load YAML config + env vars
  - Create database engine
  - Set up logging
  - Instantiate collectors or pipeline modules
  - Run worker loops with configurable batch/interval

**Collector.collect():**
- Location: Each collector in `src/aggre/collectors/*.py`
- Triggers: CLI collect command
- Responsibilities: Fetch API, parse to SilverDiscussion, store via BaseCollector helpers

**Collector.search_by_url():**
- Location: HackerNews, Lobsters collectors
- Triggers: Enrichment pipeline
- Responsibilities: Search API for URL, create new SilverDiscussion if found

**download_content():**
- Location: `src/aggre/content_fetcher.py`
- Triggers: CLI download command
- Responsibilities: HTTP GET, store raw_html, handle skips/errors

**extract_html_text():**
- Location: `src/aggre/content_fetcher.py`
- Triggers: CLI extract-html-text command
- Responsibilities: Trafilatura extraction, store body_text + title

**transcribe():**
- Location: `src/aggre/transcriber.py`
- Triggers: CLI transcribe command
- Responsibilities: Download videos, transcribe via whisper, store transcript

**enrich_content_discussions():**
- Location: `src/aggre/enrichment.py`
- Triggers: CLI enrich-content-discussions command
- Responsibilities: Search HN/Lobsters for discussions, create SilverDiscussion rows

## Error Handling

**Strategy:** Batch-tolerant with per-item exception handling

**Patterns:**

1. **Collector errors:** Individual API failures logged but don't stop batch
   ```python
   try:
       resp = client.get(url)
       resp.raise_for_status()
   except Exception:
       log.exception("collector.api_error", source=name)
       continue  # Process next item
   ```

2. **Database constraint violations:** Silent via ON CONFLICT DO NOTHING (expected duplicates)
   ```python
   stmt = pg_insert(BronzeDiscussion).on_conflict_do_nothing(...)
   ```

3. **Pipeline errors:** State transitions to FAILED with error message for later inspection
   ```python
   except Exception as exc:
       log.exception("pipeline.error", url=url)
       content_fetch_failed(engine, content_id, error=str(exc))
   ```

4. **Worker loop errors:** Logged but loop continues
   ```python
   try:
       result = fn()
   except Exception:
       log.exception(f"{name}.error")
   ```

## Cross-Cutting Concerns

**Logging:**
- Implementation: structlog with dual output (JSON file + console)
- Location: `src/aggre/logging.py`
- Pattern: All modules receive BoundLogger from CLI, use log.info/exception with dot-notation events
- Events: `{module}.{event}` (e.g., `hackernews.discussions_stored`)

**Validation:**
- URL validation: normalize_url() returns None for invalid inputs
- External ID validation: collectors check for empty external_id before storing
- No application-level schema validation (PostgreSQL handles uniqueness)

**Authentication:**
- Reddit: PRAW with credentials from env vars (AGGRE_REDDIT_* settings)
- Telegram: async TelegramClient with session string from env (AGGRE_TELEGRAM_SESSION)
- YouTube: yt-dlp with optional proxy via config.settings.proxy_url
- Other sources: Public APIs or no auth needed

**Rate Limiting:**
- Implemented: Collectors sleep between requests
  ```python
  time.sleep(rate_limit)
  client.get(url)
  ```
- Config: `reddit_rate_limit`, `hn_rate_limit`, `lobsters_rate_limit`, `telegram_rate_limit` in Settings
- Pattern: Applied per-source in collector loop, not per-item

**Concurrency:**
- Collection: ThreadPoolExecutor for parallel collector execution (max_workers = number of active collectors)
- Content fetch: ThreadPoolExecutor with configurable max_workers for parallel HTTP downloads
- Database: PostgreSQL handles concurrent writes via transactions
- Transcription: Single-threaded (whisper model cached globally to avoid GPU memory issues)

**Idempotency:**
- Duplicates: BronzeDiscussion dedup via (source_type, external_id) unique constraint
- Score/title updates: SilverDiscussion upserts mutable fields on re-insert
- Content enrichment: Tracked via enriched_at flag to avoid re-searching
- Backfill-content: Links existing discussions to SilverContent via ensure_content()

---

*Architecture analysis: 2026-02-20*
