# Decisions Log

Structural decisions made during python-guidelines.md compliance work.

## [typing] — Protocol config: BaseModel over Any — because all collector configs are Pydantic BaseModel subclasses

The `Collector` and `SearchableCollector` protocols used `config: Any`. Changed to `config: BaseModel` since every concrete collector config type (`HackernewsConfig`, `RedditConfig`, etc.) inherits from `BaseModel`. This is semantically correct and satisfies the no-Any guideline. Protocol variance is not strictly enforced by ty/mypy for this pattern, so concrete implementations with more specific config types work fine.

## [typing] — object over Any for raw_data and dict values — because the values are passed through without inspection

`_store_raw_item(raw_data: Any)` and `_upsert_discussion(values: dict[str, Any])` use `Any` for data that's JSON-serialized or passed to SQLAlchemy. Changed to `object` which is the honest type — any Python value is valid, but callers can't accidentally call methods on it without narrowing. Same for `_update_content(**values: str | int | None)` which uses a union of the actual column value types.

## [typing] — Callable[[], object] over Callable[[], Any] for run_loop fn — because return value is only logged

The `run_loop` function takes a callable and logs its return value. Changed `Callable[[], Any]` to `Callable[[], object]` since the return value is only passed to `log.info()` which accepts any object.

## [architecture] — SearchableCollector protocol over concrete types in enrichment.py — because layer 2 modules must not import each other

`enrichment.py` (layer 2) imported `HackernewsCollector` and `LobstersCollector` from `collectors/` (also layer 2). This violated the dependency layer rule. Changed the parameter types to `SearchableCollector` protocol from `collectors/base.py` (layer 1). The concrete collectors still satisfy the protocol structurally. CLI (layer 3) handles the wiring.

## [state] — model parameter over global _model_cache — because explicit dependencies are testable and traceable

`transcriber.py` had a module-level `_model_cache: WhisperModel | None` mutated by `_get_model()`. Changed to accept `model: WhisperModel | None = None` as a parameter. When `None` (first call in a batch), the function creates the model and reuses it for subsequent items in the same batch via local variable reassignment. Exposed `create_whisper_model()` for callers that want to pre-create the model.

## [http] — explicit follow_redirects parameter over **kwargs — because untyped **kwargs is implicit Any

`create_http_client()` had `**kwargs` (implicit `Any`) to pass through to `httpx.Client`. Only one caller used `follow_redirects=True`. Replaced `**kwargs` with explicit `follow_redirects: bool = False` parameter. If more httpx options are needed in the future, add them explicitly rather than reopening the `**kwargs` hole.

## [typing] — str | None for normalize_url/extract_domain — because callers pass None and the functions handle it

`normalize_url(url: str)` and `extract_domain(url: str)` both start with `if not url: return None`, but were typed as `str`-only. Tests pass `None` (which is realistic — URL fields are nullable). Changed to `str | None` to match actual behavior and fix ty type errors.

---

# Dagster Migration Decisions

## [orchestration] — Dagster over Airflow/Prefect — because medallion guidelines prescribe Dagster sensors + cursors

The medallion guidelines document explicitly prescribes Dagster sensors with `context.cursor` for incremental processing, Dagster assets for data artifacts, and Dagster schedules for batch/backfill. No reason to evaluate alternatives when the architecture doc already decided.

## [dagster-layout] — src/aggre/dagster_defs/ over separate dagster project — because single package keeps shared code accessible

Dagster definitions live inside the aggre package as `dagster_defs/` sub-package. This avoids cross-package imports for shared code (db.py, bronze.py, collectors/). The `[tool.dagster]` config in pyproject.toml points to this module.

## [bronze-storage] — directory-per-item over flat files — because medallion guidelines prescribe this pattern

Raw API responses stored as `data/bronze/{source_type}/{external_id}/raw.json`. Each item gets its own directory to colocate related artifacts (raw JSON, audio, whisper output). SQLite reserved for content-addressed caches per guidelines.

## [db-migration] — nuke all Alembic migrations — because backward compatibility explicitly abandoned

User said "screw backward compatibility, feel free to nuke code, data, anything." All existing migrations deleted. Single fresh `001_initial.py` with clean schema (no bronze_discussions, no raw_html).

## [bronze-removal] — drop bronze_discussions table — because raw data belongs in filesystem per medallion guidelines

`bronze_discussions` stored raw API JSON in PostgreSQL. Medallion guidelines: "Bronze: raw external data. Filesystem. Immutable." Moved to `data/bronze/{source_type}/{external_id}/raw.json`. PostgreSQL only holds silver (transformed, queryable).

## [fetch-status] — keep DOWNLOADED state — because two-phase content processing is still needed

Content fetching has two phases: HTTP download (raw HTML → bronze filesystem) then text extraction (trafilatura → silver body_text). DOWNLOADED marks "HTML in bronze, text not yet extracted." Simpler than trying to make it single-phase.

---

# Compliance Validation Decisions

## [typing] — dict[str, object] over bare dict in collector _store_discussion — because bare dict is implicit Any

Collector methods `_store_discussion()` used bare `dict` for API response parameters (`hit: dict`, `post_data: dict`, `story: dict`, `item: dict`). Bare `dict` is equivalent to `dict[Any, Any]` which violates the no-Any guideline. Changed to `dict[str, object]` — JSON parsed data has string keys and heterogeneous values. Same fix applied to `_fetch_json` return type and `_domain_cache` annotation.

## [typing] — RetryCallState over bare parameter in _should_retry — because untyped params violate explicit dependencies rule

`_should_retry(retry_state)` had no type annotation. The parameter is tenacity's `RetryCallState`. Imported and annotated properly. Same principle applied to `_collect_channel` in telegram collector — all 7 parameters were untyped.

## [medallion] — bronze pre-check in content_fetcher — because read-through cache pattern requires checking before fetching

`_download_one()` called `client.get(url)` without checking if the HTML was already in bronze. Added `bronze_exists_by_url()` check before the HTTP call. If bronze has the content, skip the HTTP fetch and mark as DOWNLOADED directly. This completes the read-through cache pattern prescribed by medallion guidelines.

## [docs] — removed bronze_discussions and raw_html from semantic-model.md — because schema drifted after bronze filesystem migration

`docs/semantic-model.md` still referenced `bronze_discussions` table, `raw_html` column, and `bronze_discussion_id` FK — all removed during the Dagster migration. Updated to match actual schema in `db.py`. Also updated `.planning/codebase/` analysis docs with stale references.

## [dagster] — keep Dagster files without `from __future__ import annotations` — because Dagster decorators inspect type hints at decoration time

Dagster's `@op`, `@sensor`, and `@schedule` decorators resolve type annotations at decoration time. With PEP 563 deferred annotations, these decorators fail because they can't resolve string annotations. Each affected file has a comment documenting this. Not a compliance violation — it's a documented framework limitation.

---

# Codebase-vs-Dagster Analysis Decisions

## [analysis] — Prioritized orchestration conflicts over style issues — because the Dagster migration is incomplete

The codebase was recently migrated from CLI-only to Dagster, but the migration wrapped existing code in Dagster ops/jobs without rethinking the architecture. Sensor patterns, state tracking, and orchestration helpers still follow the pre-Dagster CLI model. Focused analysis on these structural conflicts rather than surface-level style issues.

## [analysis] — Flagged status columns as orchestration anti-pattern — because medallion guidelines prescribe cursor-based sensors

Status columns on silver rows (fetch_status, transcription_status, enriched_at, comments_status) serve as both data state and orchestration triggers. The medallion guidelines explicitly prescribe Dagster cursor-based sensors for discovery. Status columns for orchestration create tight coupling between the data model and the scheduler, making it impossible to change either independently.

## [analysis] — Recommended asset migration as P1 future direction — because Dagster assets are the idiomatic data pipeline primitive

Current code uses only ops/jobs which are Dagster's lower-level primitives for arbitrary computation. Assets (`@asset`) are Dagster's higher-level primitive specifically designed for data pipelines — they provide lineage tracking, materialization events, and asset-graph visualization. The codebase's data artifacts (SilverContent, SilverDiscussion, bronze filesystem) map directly to assets.

## [analysis] — Identified generic helpers separately from domain code — because extracting reusable utilities enables parallel projects

bronze.py, bronze_http.py, http.py, and logging.py contain no Aggre-specific logic and implement patterns prescribed by medallion-guidelines.md for any project. Extracting them enables reuse without copy-paste and reduces the Aggre-specific codebase surface area.

## [analysis] — Kept telegram-auth as the one valid CLI command — because it requires interactive user input that Dagster cannot provide

Interactive auth flows (user types phone number, receives verification code) cannot run as Dagster ops. This is the correct boundary between CLI and Dagster: CLI for interactive human tasks, Dagster for automated pipeline execution.

---

# Restructuring Execution Decisions (2026-02-22)

## [structure] — Extracted generic helpers to src/aggre/utils/ — because medallion-guidelines patterns are reusable

bronze.py, bronze_http.py, http.py, and logging.py moved to utils/ sub-package. These implement medallion-guidelines.md patterns (bronze filesystem, bronze-aware HTTP, structured logging) with no Aggre-specific logic. Enables future extraction to a shared library.

## [naming] — Made _update_content public as update_content — because it's imported across 3 module boundaries

A private function imported by content_downloader.py, content_extractor.py, transcriber.py, and enrichment.py is not private. Renamed to match actual usage. No behavior change.

## [separation] — Split content_fetcher.py into content_downloader.py + content_extractor.py — because they are distinct pipeline stages

content_fetcher.py mixed HTTP download (I/O-bound, parallel) with trafilatura extraction (CPU-bound, single-threaded). These are separate Dagster ops with different concurrency characteristics. Split along the existing op boundary.

## [dedup] — Merged HN_SKIP_DOMAINS and LOBSTERS_SKIP_DOMAINS into ENRICHMENT_SKIP_DOMAINS — because they were identical

Two frozen sets with the same 4 domains ("news.ycombinator.com", "lobste.rs", "old.reddit.com", "reddit.com") existed side-by-side. Merged into single `ENRICHMENT_SKIP_DOMAINS` used by both skip checks.

## [dagster] — Reorganized dagster_defs/ by domain — because flat structure mixed concerns and blocked parallel work

Flat dagster_defs/ had jobs/ and sensors.py with all definitions. Reorganized into collection/, content/, enrichment/, transcription/ packages. Each domain owns its job + sensor/schedule. Enables independent modification of pipeline stages.

## [dagster] — Sensors accept DatabaseResource parameter instead of calling _get_engine() — because Dagster resource injection is the idiomatic pattern

Sensors previously bypassed the shared DatabaseResource and created engines directly via _get_engine(). Changed to accept `database: DatabaseResource` parameter, which Dagster injects automatically from the Definitions resource map. This follows Dagster's ConfigurableResource pattern.

## [cleanup] — Removed run-once CLI command (120 lines) and status CLI command (50 lines) — because Dagster UI replaces both

`run-once` reimplemented the entire pipeline in a single sequential loop with drain logic and TTL checks. `status` queried database for queue counts. Both are replaced by Dagster UI (job execution, sensor status, run monitoring). Also removed `all_sources_recent()` helper and `_MAX_DRAIN_ITERATIONS` constant, only used by `run-once`.
