# Plan: Resolve Dagster/Medallion Conflicts — Code Restructuring

## Goal
Reduce code, extract generic helpers, improve domain separation, and fix Dagster ecosystem conflicts identified in SUGGESTIONS.md. Each step must pass tests and linters before proceeding.

## Status: COMPLETE

All 10 steps executed. 198 tests pass, linter clean, Dagster definitions valid.

**Net change:** -356 lines (952 added, 1308 removed across 42 files)

**Commits (7 refactor + 1 docs):**
1. `e03e918` — Extract generic helpers into src/aggre/utils/
2. `5c7e8c0` — Make _update_content public as update_content
3. `dfeb4f3` — Split content_fetcher into content_downloader and content_extractor
4. `bad5296` — Merge duplicate enrichment skip-domain frozensets
5. `2325cd3` — Reorganize dagster_defs by domain with resource-injected sensors (includes Step 6)
6. `f2279fa` — Remove pre-Dagster CLI commands and all_sources_recent helper (Steps 7+8)
7. `538e300` — Update STRUCTURE.md, ARCHITECTURE.md, and DECISIONS.md

## Steps

### Step 1: Extract generic helpers into `src/aggre/utils/` ✓
### Step 2: Make `_update_content` public ✓
### Step 3: Split `content_fetcher.py` into download and extract modules ✓
### Step 4: Merge duplicated enrichment skip-domain sets ✓
### Step 5: Reorganize `dagster_defs/` by domain ✓
### Step 6: Fix sensors to use `DatabaseResource` (merged into Step 5) ✓
### Step 7: Remove redundant CLI commands ✓
### Step 8: Remove pre-Dagster orchestration helpers (merged into Step 7) ✓
### Step 9: Update stale docs ✓
### Step 10: Final verification ✓
