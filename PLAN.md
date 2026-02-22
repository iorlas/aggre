# Python Guidelines Compliance — Plan (COMPLETED)

## Assessment

The codebase is **largely compliant** with `docs/python-guidelines.md`. No overhaul needed.
31 targeted violations across 15 files. All fixable without architectural changes.

## Baseline (pre-fix)

- ruff: 31 errors (14 auto-fixable import sorts in tests)
- ty: 3 errors (test type issues — None passed to str params)

## Step 1: Fix barrel file re-exports in collector `__init__.py` files

**Files**: 7 collector sub-package `__init__.py` files
**Guideline**: §No Barrel Files — `__init__.py` contains at most `__all__`, no re-exports
**Change**: Remove import lines, keep only `__all__` declaration
**Downstream**: Update 8 test files that import via package (e.g., `from aggre.collectors.hackernews import X` → `from aggre.collectors.hackernews.collector import X`)
**Success**: No test imports use package-level re-exports; all `__init__.py` files contain only `__all__`

## Step 2: Fix unused `*Source` re-exports in config.py

**Files**: `src/aggre/config.py`, 8 test files
**Guideline**: §No Barrel Files — import from defining module, not from a package
**Change**: Remove `*Source` imports from config.py (they're re-exported with `noqa: F401`, unused in config.py). Update test imports to import `*Source` and `*Config` from their defining modules.
**Success**: No `noqa: F401` in config.py; tests import from defining modules

## Step 3: Eliminate `Any` usage

**Files**: `worker.py`, `db.py`, `collectors/base.py`
**Guideline**: §No Any — use Protocol, TypeVar, Generic, union types, or object
**Changes**:
- `worker.py`: `Callable[[], Any]` → `Callable[[], object]`
- `db.py`: `**values: Any` → `**values: str | int | None`
- `base.py`: Protocol `config: Any` → `config: BaseModel`; `raw_data: Any` → `raw_data: object`; `dict[str, Any]` → `dict[str, object]`; `source_config: dict[str, Any]` → `dict[str, object]`
**Success**: `grep -r "from typing import.*Any" src/` returns nothing; `grep -r ": Any" src/` returns nothing

## Step 4: Add missing return type annotations

**Files**: `worker.py`, `cli.py`, `collectors/base.py`
**Guideline**: §Modern Syntax — return type on every function, including `-> None`
**Changes**:
- `worker.py`: `worker_options()` → `-> Callable[[F], F]` with TypeVar, `decorator()` → `-> F`
- `cli.py`: `_cycle()` → `-> int`, `_auth()` → `-> str`, `download_cmd()` / `extract_html_text_cmd()` / `enrich_content_discussions_cmd()` / `run_once_cmd()` → `-> None`
- `base.py`: `_query_pending_comments()` → return type
**Success**: ty reports no missing return types

## Step 5: Fix layer violation in enrichment.py

**Files**: `src/aggre/enrichment.py`
**Guideline**: §Dependency Layers — Layer 2 modules never import from each other
**Change**: Replace concrete `HackernewsCollector`/`LobstersCollector` type imports with `SearchableCollector` protocol from `base.py` (layer 1). Use `TYPE_CHECKING` if protocol approach has type issues.
**Success**: enrichment.py imports nothing from `collectors/hackernews/` or `collectors/lobsters/`

## Step 6: Add missing `from __future__ import annotations`

**Files**: `src/aggre/collectors/__init__.py`
**Guideline**: §Modern Syntax — `from __future__ import annotations` in every file
**Success**: Every `.py` file in `src/` starts with `from __future__ import annotations`

## Step 7: Eliminate global mutable state in transcriber.py

**Files**: `src/aggre/transcriber.py`, `src/aggre/cli.py`
**Guideline**: §Function Design — pass all dependencies as parameters, no global state
**Change**: Remove `_model_cache` global. Add public `create_whisper_model(config)` function. Add `model: WhisperModel | None = None` parameter to `transcribe()`. Callers in cli.py create model once and pass it.
**Success**: No module-level mutable variables in `src/aggre/transcriber.py`

## Step 8: Remove backward compatibility aliases

**Files**: `src/aggre/transcriber.py`, `src/aggre/content_fetcher.py`
**Guideline**: §What to Avoid — "Avoid backwards-compatibility hacks... If something is unused, delete it completely"
**Change**: Remove `process_pending = transcribe` alias and `fetch_pending_content()` wrapper. Neither is imported anywhere.
**Success**: grep for `process_pending` and `fetch_pending_content` finds only historical git references

## Step 9: Fix `**kwargs` typing in http.py

**Files**: `src/aggre/http.py`
**Guideline**: §No Any — `**kwargs` without annotation is implicitly `Any`
**Change**: Replace `**kwargs` with explicit `follow_redirects: bool = False` parameter (the only extra kwarg used in the codebase)
**Success**: No untyped `**kwargs` in src/

## Step 10: Fix pre-existing test type errors

**Files**: `tests/test_telegram.py`, `tests/test_urls.py`
**Change**: Fix `None` being passed where `str` expected — use proper types or adjust function signatures
**Success**: `ty check src tests` reports 0 errors

## Step 11: Fix import sorting in tests

**Change**: Run `ruff check --fix tests/` to auto-fix import sorting
**Success**: `ruff check src tests` reports 0 errors

## Step 12: Final verification

- Full test suite passes
- `ruff check src tests` — 0 errors
- `ruff format --check src tests` — 0 errors
- `ty check src tests` — 0 errors
