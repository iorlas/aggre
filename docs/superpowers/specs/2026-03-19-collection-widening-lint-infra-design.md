# Collection Widening & Lint Infrastructure

**Date:** 2026-03-19
**Status:** Draft

## Problem

1. HN collector fetches only 100 front-page items/hour — misses stories that never reach front page or fall off quickly
2. Lobsters collector fetches ~50 items/hour — too narrow
3. Discussion search (per-link API calls) was disabled because it overwhelmed hardware and risked Algolia throttling
4. RSS collection is broken — pydantic validation error in `rss_collection.py:27`
5. No CI gate catches type errors — pre-commit hook not installed, `uvx`/`uv run` divergence
6. `make lint` output is verbose and not optimized for AI agents

## Design

### A. HN Collection Widening

**File:** `src/aggre/collectors/hackernews/collector.py`

Change the Algolia query from `tags=story,front_page` to `tags=story` and set `hitsPerPage=1000`.

This fetches all new stories posted to HN, not just what's currently on the front page. Upsert via `ON CONFLICT DO UPDATE` refreshes scores/comment counts for already-seen items.

**Config:** `HackernewsConfig.fetch_limit` default changes from `100` to `1000`.

**Risk:** 1 request/hour = 0.01% of Algolia's 10,000/hour/IP limit. No auth required. No documented throttling reports from any user.

**Self-posts:** Already handled — collector checks `if hit.get("url")` and routes to self-post path when empty.

**Volume impact:** Collecting all stories instead of front-page only increases ingestion volume significantly (~1000 items/hour vs ~100). Most will be low-engagement posts. Downstream dedup (`text IS NOT NULL` skip in event emission) prevents re-processing of already-handled items. The wider net is intentional — organic URL matching depends on having more discussions in the database.

### B. Lobsters Collection Widening

**File:** `src/aggre/collectors/lobsters/collector.py`

Paginate `/hottest.json` and `/newest.json`. Fetch pages 1 through N (configurable) for each endpoint.

**Config:** Add `pages: int = 4` to `LobstersConfig`. Total items per cycle: up to 8 requests x 25 items = 200. Deduplication by `short_id` already happens via `stories_by_id` dict.

**Rate limit:** 8 requests x 2s = 16 seconds per cycle. Acceptable.

**Pagination:** Use `?page=N` query parameter (1-indexed). Works for both hottest and newest endpoints. Tag URLs (`/t/{tag}.json`) should also be paginated for consistency.

### C. RSS Pydantic Bug Fix

**File:** `src/aggre/workflows/rss_collection.py:25-27`

**Bug:** `collect_source()` returns `CollectResult` object but line 27 passes it as `succeeded` and `total` (both expect `int`).

**Fix:** Replace lines 25-27:
```python
result = collect_source(engine, cfg, "rss", RssCollector, source_config=single_config, hatchet=h)
ctx.log(f"Collected {result.succeeded} from {input.name}")
return result
```

Return the `CollectResult` directly. Source field will be `"rss"`, consistent with all other collectors.

### D. Remove Discussion Search Code

URL matching already works via `content_id` — when multiple sources collect stories pointing to the same URL, they share the same `SilverContent` row. Wider collection eliminates the need for per-link API search.

**Delete:**
- `HackernewsCollector.search_by_url()` method
- `LobstersCollector.search_by_url()` method
- `LobstersCollector._domain_cache` dict and `__init__` method
- `SearchableCollector` protocol from `base.py`
- Test classes: `TestHackernewsSearchByUrl`, `TestLobstersSearchByUrl` and all their methods

**Keep:** `fetch_discussion_comments()` — actively used by comments workflow for fetching comment trees.

**Clean up docs (explicit list):**
- `docs/guidelines/semantic-model.md` — remove `discussions_searched_at` column reference (line 36), `idx_content_needs_discussion_search` index (line 40), and "Discussion search coverage" query recipe (lines 369-386). Column was dropped in migration 011.
- `.planning/codebase/STRUCTURE.md` — remove reference to `src/aggre/dagster_defs/discussion_search/`
- `.planning/codebase/INTEGRATIONS.md` — update "Enrichment" section that references discussion search
- `.planning/codebase/TESTING.md` — remove reference to `test_discussion_search.py`

### E. Lint Infrastructure

#### E1. Makefile targets

Two targets, clear separation:

```makefile
lint:  ## Check only — safe for AI, CI, pre-commit. Never modifies files.
	@uv run ruff format --check || (echo "Formatting issues found. Run 'make fix' to auto-fix." && exit 1)
	@uv run ruff check || (echo "Lint issues found. Fixable ones can be resolved with 'make fix'." && exit 1)
	@uv run ty check
	@uv run yamllint -c .yamllint.yml .
	@uv run python scripts/check-json.py

fix:  ## Auto-fix formatting and import sorting. Modifies files.
	uv run ruff check --fix
	uv run ruff format
```

All lint commands use `@` prefix to suppress command echo (less noise). `output-format = "concise"` is set in `pyproject.toml` (not as CLI flag) so it applies everywhere.

**Why two targets:** `make lint` is read-only — safe for AI agents mid-work (Claude Code doesn't track file changes from bash commands). `make fix` mutates files — run by humans or pre-commit before commit. AI-first hint messages guide the agent to `make fix` when formatting issues are found.

**No folder hardcoding:** ruff and ty read paths from `pyproject.toml` config.

#### E2. pyproject.toml changes

```toml
[tool.ruff]
output-format = "concise"
```

Add `yamllint` to dev dependencies (already done). Keep `check-jsonschema` for future schema validation.

#### E3. New files

**`.yamllint.yml`** — config with `ignore` patterns:
- `node_modules/`
- `.venv/`
- `.dmux/`
- `data/`
- `tests/collectors/cassettes/`
- `.git/`

**`scripts/check-json.py`** — JSON syntax validator using stdlib `json` module. Same exclusion set as yamllint. Exits non-zero on any invalid JSON.

#### E4. Pre-commit

Replace `.pre-commit-config.yaml` contents with two hooks:
1. `make fix` — auto-fix stage (prek/pre-commit handles re-staging modified files automatically)
2. `make lint` — check stage (fails commit if unfixable issues remain)

Fix `uvx ty check .` → eliminated (now runs through `make lint` which uses `uv run`).

Document `prek` as the recommended pre-commit runner (Rust, 7-10x faster, drop-in compatible). Installation: `brew install prek`.

#### E5. Fix existing ruff errors

- Import sorting in `src/aggre/collectors/arxiv/collector.py`
- Import sorting in `src/aggre/collectors/youtube/collector.py`
- Unused imports in `tests/utils/test_ytdlp.py` and `tests/workflows/test_transcription.py`
- `VideoUnavailable` naming (N818) — rename to `VideoUnavailableError` or suppress rule

#### E6. CLAUDE.md update

```
- Lint: `make lint` (check only, never modifies files)
- Fix: `make fix` (auto-fix formatting and import sorting)
```

## What We're NOT Doing

- **BigQuery sync** — 12-24h stale, no SLA, silently broke for 18 months. Dead for real-time needs.
- **Discussion search** — replaced by wider collection + existing `content_id` URL matching.
- **Hadolint** — noise for this project (one false positive on main Dockerfile).
- **actionlint** — add when CI grows. Only 1 workflow file now.
- **Parallel lint execution** — 2 seconds total, not worth the complexity. Mixed output would confuse AI.
- **Claude Code hooks for linting** — pre-commit + CI is the right layer. Hooks mid-change cause stale file issues.
- **GitHub Actions** — future work. `make lint` is designed to be CI-ready when we add it.

## Follow-up

- Capture Python AI engineering findings in R019 (knowledge base research)
- Add GitHub Actions CI workflow calling `make lint` + `make test-e2e`
- Add `actionlint` to `make lint` when CI workflow count grows
