# Suggestions: Conflicting Concepts with Dagster Ecosystem & Medallion Guidelines

Analysis of code patterns that conflict with Dagster idioms, medallion-guidelines.md prescriptions, and opportunities for simplification after the Dagster migration.

---

## 1. Sensor Anti-Pattern: Status Columns as Orchestration Triggers

**Problem**: All three sensors poll silver-layer status columns on `SilverContent` to decide when to trigger jobs. This makes the silver data model serve double duty: storing domain state AND driving orchestration. This is an anti-pattern for two reasons:

1. **Dagster sensors should use cursors**, not status column polling. The medallion guidelines prescribe: "Sensor: lightweight (<5 sec). Cursor = `context.cursor` — stored by Dagster automatically. Tracks last processed position." Current sensors ignore `context.cursor` entirely — it's only used to generate unique `run_key` values.

2. **Silver records indicating status of the next task is layer confusion.** `SilverContent.fetch_status = PENDING` means "this content needs fetching" — that's orchestration state, not data state. `SilverContent.enriched_at IS NULL` means "enrichment hasn't run yet" — also orchestration. The silver layer should hold transformed data; scheduling concerns belong in the orchestration layer (Dagster).

**Affected files:**
- `src/aggre/dagster_defs/sensors.py:39-88` — all three sensors
- `src/aggre/db.py:40` — `fetch_status` column
- `src/aggre/db.py:45` — `transcription_status` column
- `src/aggre/db.py:49` — `enriched_at` column
- `src/aggre/db.py:68` — `comments_status` column

**Specific instances:**

| Sensor | Polls | Anti-pattern |
|--------|-------|-------------|
| `content_sensor` | `SilverContent.fetch_status IN (PENDING, DOWNLOADED)` | Parent row status drives child task scheduling |
| `enrichment_sensor` | `SilverContent.enriched_at IS NULL` | NULL-as-not-done is orchestration state on a data row |
| `transcription_sensor` | `SilverContent.transcription_status == PENDING` | Parent row status drives child task scheduling |
| *(no sensor)* | `SilverDiscussion.comments_status == PENDING` | Queried directly in collector code, not via Dagster at all |

**Suggested approach**: Use Dagster cursor-based sensors that track "last processed ID" or "last processed timestamp." The sensor emits `RunRequest` with run config containing the IDs to process, rather than having ops re-query the database for pending items. Status columns can remain for observability but should not be the orchestration mechanism.

---

## 2. No Dagster Cursor Usage

**Problem**: All three sensors create a new engine, query the database, and check counts — but none use `context.cursor` to track progress. The `context.cursor` is only used to construct a unique `run_key` string (e.g., `f"content-{context.cursor or 0}"`), and is never actually updated via `context.update_cursor()`.

**Affected files:**
- `src/aggre/dagster_defs/sensors.py:50-53` — `content_sensor` uses cursor for run_key only
- `src/aggre/dagster_defs/sensors.py:68-71` — `enrichment_sensor` same pattern
- `src/aggre/dagster_defs/sensors.py:84-87` — `transcription_sensor` same pattern

**Why this matters**: Without cursor tracking, sensors can't do delta detection. Every sensor tick re-scans the full table. If a job fails mid-batch, the sensor will re-trigger for the same items. With proper cursor usage, the sensor tracks "last ID processed" and only looks at new items.

**Suggested approach**: Sensors should `context.update_cursor(str(max_id_processed))` after each successful run. On next tick, query `WHERE id > cursor`. This is exactly the pattern prescribed by medallion guidelines: "Cursor = mtime or index offset."

---

## 3. Sensors Create Their Own Database Engine

**Problem**: Each sensor calls `_get_engine()` which creates a fresh `Settings()` and `get_engine()` on every tick. This bypasses the `DatabaseResource` defined in `resources.py`, creating an inconsistent pattern.

**Affected files:**
- `src/aggre/dagster_defs/sensors.py:20-23` — `_get_engine()` function
- `src/aggre/dagster_defs/sensors.py:42,60,78` — each sensor calls `_get_engine()`
- `src/aggre/dagster_defs/resources.py:11-17` — `DatabaseResource` defined but not used by sensors

**Why this matters**: Resources are Dagster's mechanism for dependency injection. Sensors should use `build_resources` or the sensor resource API to share the `DatabaseResource` with jobs.

---

## 4. Jobs Re-Load Config on Every Op Execution

**Problem**: Every op calls `load_config()` independently, reading and parsing `config.yaml` from disk on each invocation. Config should be loaded once and passed through Dagster's resource/config system.

**Affected files:**
- `src/aggre/dagster_defs/jobs/collect.py:19`
- `src/aggre/dagster_defs/jobs/content.py:19,28`
- `src/aggre/dagster_defs/jobs/enrich.py:21`
- `src/aggre/dagster_defs/jobs/transcribe.py:19`

**Suggested approach**: Create a `ConfigResource` (similar to `DatabaseResource`) that loads config once and provides it to all ops. This aligns with Dagster's resource pattern and avoids repeated disk I/O.

---

## 5. Jobs Create Their Own Loggers Instead of Using Dagster Context

**Problem**: Every op calls `setup_logging(cfg.settings.log_dir, "...")` to create a structlog logger, ignoring Dagster's built-in `context.log`. This means:
- Dagster UI won't show structured log events from ops
- Log configuration diverges between Dagster and custom logging
- Dual logging (Dagster's + custom) creates confusion

**Affected files:**
- `src/aggre/dagster_defs/jobs/collect.py:21` — `setup_logging(cfg.settings.log_dir, "collect")`
- `src/aggre/dagster_defs/jobs/content.py:21,30`
- `src/aggre/dagster_defs/jobs/enrich.py:23`
- `src/aggre/dagster_defs/jobs/transcribe.py:21`

**Suggested approach**: Pass `context.log` to business functions, or create a Dagster-aware logging adapter. Business functions already accept a `log` parameter — the interface is ready, just the implementation needs to bridge to Dagster.

---

## 6. CLI `run-once` Duplicates Dagster Orchestration

**Problem**: The `run-once` CLI command (`src/aggre/cli.py:117-239`) reimplements the entire pipeline orchestration with drain loops, stage sequencing, and status reporting. Now that Dagster handles orchestration, this is ~120 lines of redundant code. The drain loop pattern (`for _ in range(_MAX_DRAIN_ITERATIONS)`) is a manual reimplementation of what Dagster sensors do automatically.

**Affected files:**
- `src/aggre/cli.py:114` — `_MAX_DRAIN_ITERATIONS = 100`
- `src/aggre/cli.py:117-239` — entire `run_once_cmd` function
- `src/aggre/cli.py:128-133` — imports of business functions also imported by Dagster ops

**Suggested approach**: Keep a minimal `run-once` that simply triggers Dagster jobs via the Dagster CLI or Python API (`dagster job execute`), or remove entirely and document `dagster job execute -j collect_job` as the replacement. If dev convenience is needed, a thin wrapper that calls `dagster job execute` for each job in sequence would suffice.

---

## 7. CLI `status` Command Duplicates Dagster UI

**Problem**: The `status` command (`src/aggre/cli.py:58-111`) queries the database directly to show collection times, queue sizes, and errors. This is exactly what the Dagster UI provides: job run history, sensor status, and op logs. The ~53 lines of status rendering code becomes redundant.

**Affected files:**
- `src/aggre/cli.py:58-111` — entire `status` function

**Suggested approach**: Remove or deprecate. Users should use `dagster dev` → localhost:3000 for operational visibility. If CLI status is still desired, it should query Dagster's GraphQL API or run metadata, not the silver database directly.

---

## 8. BaseCollector Orchestration Helpers Are Pre-Dagster Artifacts

**Problem**: Several `BaseCollector` methods exist solely to support CLI-driven orchestration and are redundant with Dagster:

| Method | Purpose | Dagster equivalent |
|--------|---------|-------------------|
| `_is_source_recent()` | Skip collection if source was fetched recently | Schedule cron interval |
| `_get_fetch_limit()` | Different limits for first vs subsequent fetches | Job config / partition |
| `_is_initialized()` | Check if source has been fetched once | Dagster run history |
| `all_sources_recent()` | Check if ALL sources of a type are fresh | Schedule/sensor state |

**Affected files:**
- `src/aggre/collectors/base.py:22-48` — `all_sources_recent()`
- `src/aggre/collectors/base.py:93-115` — `_is_initialized()`, `_get_fetch_limit()`, `_is_source_recent()`

**Suggested approach**: Move scheduling decisions to Dagster. Remove TTL-checking methods from BaseCollector. Use Dagster schedules for timing, run config for limits.

---

## 9. Comments Collection Not Integrated with Dagster

**Problem**: Comments collection (`comments_status` tracking on `SilverDiscussion`) is handled inside the `collect_job` op as an afterthought — a loop over three hardcoded source names with `hasattr` checks. There's no Dagster sensor watching for pending comments, and no separate job for it.

**Affected files:**
- `src/aggre/dagster_defs/jobs/collect.py:36-42` — hardcoded loop with `hasattr` check
- `src/aggre/collectors/base.py:117-146` — `_query_pending_comments()`, `_mark_comments_done()`
- `src/aggre/db.py:68` — `comments_status` column

**Why this matters**: Comments collection is a separate concern from discussion collection. It's rate-limited differently, can fail independently, and should be independently observable. Bundling it in `collect_job` means a failed comment fetch can delay the next collection cycle.

**Suggested approach**: Create a separate `comments_job` with its own sensor, or make it a separate op within the collect job graph with proper dependency edges. Remove the `hasattr` check — use the `Collector` protocol or a separate `CommentCollector` protocol.

---

## 10. Dagster Definition Organization — Single-File Sensors

**Problem**: All sensors and the schedule are in one file (`sensors.py:88 lines`). Each sensor targets a different job and serves a different domain concern (content fetching, enrichment, transcription). As sensors grow more complex (e.g., adding cursor logic), this file will become a merge conflict hotspot.

**Current layout:**
```
dagster_defs/
├── __init__.py      # 26 lines — all definitions composed here
├── resources.py     # 17 lines — DatabaseResource
├── sensors.py       # 88 lines — 3 sensors + 1 schedule (mixed concerns)
└── jobs/
    ├── __init__.py
    ├── collect.py   # 50 lines
    ├── content.py   # 36 lines
    ├── enrich.py    # 36 lines
    └── transcribe.py # 27 lines
```

**Suggested layout (domain-aligned):**
```
dagster_defs/
├── __init__.py          # compose all definitions
├── resources.py         # shared resources
├── collection/          # collection domain
│   ├── job.py           # collect_job + collect ops
│   ├── schedule.py      # collection_schedule
│   └── comments_job.py  # separate comments job + sensor
├── content/             # content fetching domain
│   ├── job.py           # content_job + ops
│   └── sensor.py        # content_sensor
├── enrichment/          # enrichment domain
│   ├── job.py           # enrich_job + ops
│   └── sensor.py        # enrichment_sensor
└── transcription/       # transcription domain
    ├── job.py           # transcribe_job + ops
    └── sensor.py        # transcription_sensor
```

This aligns with the python-guidelines.md principle: "would two AI agents ever touch this same file simultaneously?" Currently, changing the enrichment sensor requires editing the same file as changing the transcription sensor.

---

## 11. Generic Helpers That Should Be Extracted

Several modules contain logic that is not Aggre-specific and could be reused across projects. Extracting them into a separate utilities package (or at minimum, a `utils/` sub-package) would reduce coupling and enable parallel development.

### Candidates for extraction:

| Module | Lines | Generic? | Rationale |
|--------|-------|----------|-----------|
| `bronze.py` | 146 | **Yes** — generic bronze filesystem ops | Path construction, atomic writes, read/write JSON, URL hashing — all reusable for any medallion-architecture project |
| `bronze_http.py` | 71 | **Yes** — generic read-through HTTP cache | Bronze-aware HTTP wrapper is the exact pattern described in medallion guidelines for any project |
| `http.py` | 24 | **Yes** — generic HTTP client factory | Creates httpx.Client with common defaults. No Aggre-specific logic |
| `logging.py` | 67 | **Yes** — generic structlog setup | Dual-output (JSON file + console) logging config. No Aggre-specific logic |
| `urls.py` | 166 | **Partially** | `normalize_url()` has Aggre-specific domain rules (arxiv, youtube, github). `extract_domain()` and generic URL cleaning are reusable. `ensure_content()` is Aggre-specific |
| `statuses.py` | 32 | **No** — Aggre-specific enums | `FetchStatus`, `TranscriptionStatus`, `CommentsStatus` are domain-specific |
| `db.py` | 106 | **No** — Aggre-specific ORM models | Schema-specific |
| `settings.py` | 30 | **No** — Aggre-specific env vars | Contains Aggre-specific settings |
| `config.py` | 42 | **No** — Aggre-specific config loading | Composes Aggre-specific collector configs |

**Suggested structure:**
```
src/aggre/
├── utils/               # Generic, reusable across projects
│   ├── bronze.py        # Bronze filesystem operations
│   ├── bronze_http.py   # Bronze-aware HTTP wrapper
│   ├── http.py          # HTTP client factory
│   └── logging.py       # Structured logging setup
├── dagster_utils/       # Generic Dagster helpers (if any emerge)
│   └── ...
├── urls.py              # Split: generic URL cleaning → utils/, domain-specific stays
├── db.py                # Aggre-specific
├── statuses.py          # Aggre-specific
├── settings.py          # Aggre-specific
├── config.py            # Aggre-specific
└── ...
```

---

## 12. `_update_content` Is a Leaky Abstraction

**Problem**: `db.py:103-106` defines `_update_content()` (private, prefixed with `_`) but it's imported and used across 3 different modules:
- `src/aggre/content_fetcher.py:14` — imports `_update_content`
- `src/aggre/enrichment.py:10` — imports `_update_content`
- `src/aggre/transcriber.py:14` — imports `_update_content`

A private function used across module boundaries is a code smell. It should either be made public (rename to `update_content`) or each module should own its own update logic.

**Suggested approach**: Make it a public function `update_content()` in `db.py`, or better, have each domain module (content_fetcher, enrichment, transcriber) own its own state transition functions that write directly to the database — removing the shared mutable helper.

---

## 13. `collect_job` Op Does Too Much

**Problem**: `collect_all_sources` op in `collect.py:17-45` performs two distinct operations in a single op:
1. Collect discussions from all sources (lines 26-33)
2. Fetch comments for reddit/hackernews/lobsters (lines 36-42)

These are independent concerns with different failure modes, different rate limits, and should be separately observable in Dagster UI.

**Affected files:**
- `src/aggre/dagster_defs/jobs/collect.py:17-45`

**Suggested approach**: Split into `collect_discussions_op` and `collect_comments_op` as separate ops in a job graph. This makes each independently retryable and observable.

---

## 14. Medallion Guideline: Missing Sensor for Bronze→Silver Discovery

**Problem**: The medallion guidelines prescribe: "Bronze→Silver sensor: watches filesystem. Cursor = mtime or index offset." Currently, no sensor watches the bronze filesystem for new raw data. Instead, sensors watch silver status columns — the exact opposite direction.

For the "separate pipes" pattern (prescribed for expensive sources), the flow should be:
1. Fetch worker: external → bronze
2. Sensor: watches bronze filesystem
3. Transform worker: bronze → silver (triggered by sensor)

Currently, the flow is:
1. Collector: external → bronze + silver simultaneously (combined pipe)
2. Sensor: watches silver status columns
3. Downstream ops: read from bronze, update silver

**Affected files:**
- `src/aggre/dagster_defs/sensors.py` — all sensors poll silver, none watch bronze
- `src/aggre/collectors/base.py:84-86` — `_write_bronze()` writes to filesystem
- Collectors write both bronze (filesystem) and silver (DB) in the same operation

**Suggested approach**: For the current combined-pipe model, this is a minor concern — collectors write both layers atomically. But if moving to separate pipes (which the guidelines recommend for expensive sources like YouTube), sensors should watch bronze for new raw data, not silver for status column changes.

---

## 15. Medallion Guideline: Enrichment Skip-Domain Sets Are Duplicated

**Problem**: `enrichment.py:12-34` defines two identical `frozenset`s — `HN_SKIP_DOMAINS` and `LOBSTERS_SKIP_DOMAINS` — with the exact same values. This is pure duplication.

**Affected files:**
- `src/aggre/enrichment.py:12-34`

**Suggested approach**: Merge into a single `ENRICHMENT_SKIP_DOMAINS` frozenset, or if they need to diverge in the future, document why they're separate.

---

## 16. `content_fetcher.py` Mixes Download and Extract in Same Module

**Problem**: `content_fetcher.py` (209 lines) contains two distinct pipeline stages:
1. `download_content()` — HTTP download → bronze (lines 118-154)
2. `extract_html_text()` — bronze → silver text extraction (lines 157-209)

These are separate Dagster ops (`download_content_op` and `extract_content_op`) but live in the same file. They have different concurrency models (parallel HTTP vs single-threaded CPU), different error modes, and different dependencies.

**Affected files:**
- `src/aggre/content_fetcher.py` — two stages in one file

**Suggested approach**: Split into `content_downloader.py` (download) and `content_extractor.py` (extract). This aligns module boundaries with Dagster op boundaries and enables parallel agent work.

---

## 17. State Transition Functions Could Be Consolidated

**Problem**: State transition functions are scattered across modules:
- `content_fetcher.py:40-57` — 4 functions: `content_skipped`, `content_downloaded`, `content_fetched`, `content_fetch_failed`
- `transcriber.py:20-39` — 4 functions: `transcription_downloading`, `transcription_transcribing`, `transcription_completed`, `transcription_failed`
- `collectors/base.py:129-146` — `_mark_comments_done` (transition logic)

Each function is a thin wrapper around `_update_content(engine, id, status=...)`. The pattern is identical — only the column names and values differ.

**Suggested approach**: Keep transition functions co-located with their domain logic (current approach is acceptable). But consider whether the `_update_content` indirection adds value or just obscures what's happening. Direct `sa.update()` calls in each function would be more explicit.

---

## 18. `Source.last_fetched_at` Is Orchestration State on a Data Row

**Problem**: `Source.last_fetched_at` tracks when a source was last collected. This is orchestration state (used by `_is_source_recent()`, `all_sources_recent()`) stored on a data entity. With Dagster, this information is available in Dagster's run history.

**Affected files:**
- `src/aggre/db.py:29` — `last_fetched_at` column
- `src/aggre/collectors/base.py:88-91` — `_update_last_fetched()`
- `src/aggre/collectors/base.py:93-115` — methods that query `last_fetched_at`

**Suggested approach**: Keep `last_fetched_at` for observability (it's useful in the `status` command and for debugging), but stop using it for orchestration decisions. Let Dagster schedules control timing.

---

## 19. `telegram-auth` CLI Command Is Still Needed

**Problem (non-problem)**: The `telegram-auth` command (`cli.py:30-55`) is an interactive auth flow that generates a session string. This is NOT replaceable by Dagster — it's a one-time setup step that requires user interaction.

**Decision**: Keep this command. It's the only CLI command that has no Dagster equivalent.

---

## 20. No Dagster Assets — Everything Is Jobs/Ops

**Problem**: The codebase uses only Dagster's `@op` and `@job` primitives. Dagster's asset model (`@asset`, `@multi_asset`) is a better fit for data pipeline work because:
- Assets represent data artifacts (SilverContent, SilverDiscussion) with lineage
- Asset materialization events provide built-in observability
- Asset sensors can trigger downstream processing automatically
- The Dagster UI shows the asset graph with materialization status

Current ops are stateless functions that happen to write to the database. There's no lineage tracking, no materialization metadata, and no asset graph in the UI.

**Suggested approach**: Consider migrating to Dagster assets for the data artifacts:
- `bronze_discussions` asset — raw data per source type
- `silver_discussions` asset — transformed discussions
- `silver_content` asset — content artifacts
- Each asset declares its upstream dependency

This is a larger refactor but would align fully with Dagster idioms.

---

## Summary: Priority Matrix

| # | Suggestion | Impact | Effort | Priority |
|---|-----------|--------|--------|----------|
| 1 | Sensor status column anti-pattern | High | High | P1 — architectural |
| 2 | No Dagster cursor usage | High | Medium | P1 — correctness |
| 3 | Sensors bypass DatabaseResource | Medium | Low | P2 — consistency |
| 4 | Ops re-load config every time | Low | Low | P3 — optimization |
| 5 | Ops create own loggers | Medium | Medium | P2 — observability |
| 6 | CLI `run-once` redundancy | Medium | Low | P2 — code reduction |
| 7 | CLI `status` redundancy | Low | Low | P3 — code reduction |
| 8 | BaseCollector orchestration helpers | Medium | Medium | P2 — simplification |
| 9 | Comments not Dagster-integrated | Medium | Medium | P2 — completeness |
| 10 | Dagster definition organization | Medium | Medium | P2 — maintainability |
| 11 | Generic helpers extraction | Medium | Medium | P2 — reusability |
| 12 | `_update_content` leaky abstraction | Low | Low | P3 — code hygiene |
| 13 | `collect_job` op does too much | Medium | Low | P2 — observability |
| 14 | No bronze→silver sensor | Low | High | P3 — guidelines alignment |
| 15 | Duplicated skip-domain sets | Low | Low | P3 — DRY |
| 16 | content_fetcher mixes stages | Medium | Low | P2 — separation |
| 17 | Scattered state transitions | Low | Low | P3 — optional cleanup |
| 18 | `last_fetched_at` orchestration | Low | Low | P3 — clarification |
| 19 | Keep telegram-auth command | N/A | N/A | No action needed |
| 20 | No Dagster assets | High | High | P1 — future direction |
