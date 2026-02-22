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
