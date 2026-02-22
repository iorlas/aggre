# Plan: Reorganize src/aggre Root and Enhance Utils

## Problem
- 11 files in `src/aggre/` root — mix of infrastructure and business pipeline modules
- `utils/` has 4 files, all data-layer related; missing generic DB, URL, and HTTP utilities
- Duplicate `content_fetch_failed` in both `content_downloader.py` and `content_extractor.py`
- HTTP client lifecycle managed with manual try/finally in 9+ collector methods
- Generic helpers (`now_iso`, `get_engine`, `extract_domain`) trapped in domain-specific modules

## Goal
Reduce root clutter, extract reusable utils (for future data/pipeline/backend/AI projects), eliminate duplicates, and shift emphasis from plumbing to business logic.

## Steps

### Step 1: Create `pipeline/` subpackage (root cleanup) ✅
Move 4 business pipeline modules out of root:
- `content_downloader.py` → `pipeline/content_downloader.py`
- `content_extractor.py` → `pipeline/content_extractor.py`
- `transcriber.py` → `pipeline/transcriber.py`
- `enrichment.py` → `pipeline/enrichment.py`
- Create `pipeline/__init__.py`
- Update ALL imports (dagster_defs, tests)
- **Success:** Root drops from 11 → 7 files; pipeline modules grouped by domain

### Step 2: Create `utils/db.py` (generic DB utils) ✅
Extract from `db.py`:
- `now_iso()` — used in 5+ files, pure utility
- `get_engine()` — used in 4 files, pure factory
- Update all imports across source + tests
- **Success:** `db.py` contains only ORM models + Aggre-specific helpers; generic DB tools are in utils

### Step 3: Create `utils/urls.py` (generic URL utils) ✅
Extract from `urls.py`:
- `extract_domain()` — generic domain extraction
- `TRACKING_PARAMS` — reusable frozenset
- `strip_tracking_params(query)` — new function extracted from normalize_url's redundant tracking param cleaning
- Refactor `normalize_url()` to use `strip_tracking_params()` (fixes code duplication in lines 107-110 + 121-123)
- **Success:** Generic URL tools in utils; normalize_url cleaner

### Step 4: Consolidate duplicate `content_fetch_failed` ✅
- Remove duplicate from `content_extractor.py`
- Have `content_extractor.py` import from `content_downloader.py`
- **Success:** Single source of truth for fetch failure transition

### Step 5: Use HTTP context manager in collectors ✅
- `httpx.Client` is already a context manager; `create_http_client()` returns one
- Convert try/finally blocks in collectors to `with create_http_client(...) as client:`
- **Success:** ~27 lines eliminated; cleaner resource management

### Step 6: Update documentation ✅
- `STRUCTURE.md` — new directory layout
- `ARCHITECTURE.md` — update layer descriptions
- **Success:** Docs match code

## After — Root Layout
```
src/aggre/
├── __init__.py
├── cli.py
├── config.py
├── db.py
├── settings.py
├── statuses.py
├── urls.py
├── collectors/
├── dagster_defs/
├── pipeline/        ← NEW
│   ├── __init__.py
│   ├── content_downloader.py
│   ├── content_extractor.py
│   ├── enrichment.py
│   └── transcriber.py
└── utils/
    ├── __init__.py
    ├── bronze.py
    ├── bronze_http.py
    ├── db.py          ← NEW
    ├── http.py
    ├── logging.py
    └── urls.py        ← NEW
```

## After — Utils Catalog (reusable across projects)
| Module | Domain | Functions |
|--------|--------|-----------|
| `utils/bronze.py` | Data Engineering | Immutable filesystem storage (medallion pattern) |
| `utils/bronze_http.py` | Data Engineering | HTTP fetch with bronze read-through cache |
| `utils/db.py` | Backend Engineering | `now_iso()`, `get_engine()` |
| `utils/http.py` | Python Engineering | HTTP client factory with proxy + UA |
| `utils/logging.py` | Python Engineering | Structured logging (structlog dual output) |
| `utils/urls.py` | Python Engineering | `extract_domain()`, `strip_tracking_params()` |
