# Collection Widening & Lint Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen HN and Lobsters collection to catch more stories (eliminating the need for discussion search), fix RSS pydantic bug, remove dead discussion search code, and restructure lint infrastructure for AI-safe workflows.

**Architecture:** The collection changes are isolated to collector files and configs. The lint changes touch Makefile, pyproject.toml, pre-commit config, and add two new files. No database migrations needed.

**Tech Stack:** Python 3.12, ruff, ty, yamllint, prek, Algolia HN API, Lobsters JSON API

**Spec:** `docs/superpowers/specs/2026-03-19-collection-widening-lint-infra-design.md`

**Test commands:**
- Run all tests: `make test-e2e` (spins up ephemeral postgres)
- Run specific test file: `AGGRE_TEST_DATABASE_URL=postgresql+psycopg://aggre:aggre@localhost:5433/aggre_test uv run pytest tests/path/file.py -v`
- Lint: `make lint`
- Relevant guidelines: `docs/guidelines/testing.md`, `docs/guidelines/python.md`

---

## Task 1: Fix Existing Ruff Errors

These must be fixed first so `make lint` passes cleanly before we change anything else.

**Files:**
- Modify: `src/aggre/collectors/arxiv/collector.py` (import sorting)
- Modify: `src/aggre/collectors/youtube/collector.py` (import sorting)
- Modify: `tests/utils/test_ytdlp.py` (unused imports, f-string)
- Modify: `tests/workflows/test_transcription.py` (unused import)
- Modify: `src/aggre/utils/ytdlp.py` (N818 naming)

- [ ] **Step 1: Auto-fix import sorting and unused imports**

```bash
uv run ruff check --fix src/aggre/collectors/arxiv/collector.py src/aggre/collectors/youtube/collector.py tests/utils/test_ytdlp.py tests/workflows/test_transcription.py
```

- [ ] **Step 2: Rename `VideoUnavailable` to `VideoUnavailableError`**

In `src/aggre/utils/ytdlp.py`, rename the class:
```python
class VideoUnavailableError(YtDlpError):
    """Permanent — video deleted, private, region-blocked. Safe to skip."""
```

Then update all references. Search for `VideoUnavailable` across the codebase:
- `src/aggre/collectors/youtube/collector.py` — import and except clause
- `src/aggre/workflows/transcription.py` — import and except clause
- `tests/utils/test_ytdlp.py` — import and assertions
- `tests/workflows/test_transcription.py` — import and assertions

- [ ] **Step 3: Verify ruff passes**

```bash
uv run ruff check src tests
```

Expected: 0 errors.

- [ ] **Step 4: Verify ty passes**

```bash
uv run ty check src tests
```

Expected: "All checks passed!"

- [ ] **Step 5: Commit**

```bash
git add src/aggre/collectors/arxiv/collector.py src/aggre/collectors/youtube/collector.py src/aggre/utils/ytdlp.py tests/utils/test_ytdlp.py tests/workflows/test_transcription.py
git commit -m "fix: resolve existing ruff errors — import sorting, unused imports, N818 naming"
```

---

## Task 2: Lint Infrastructure

**Files:**
- Modify: `Makefile`
- Modify: `pyproject.toml`
- Modify: `.pre-commit-config.yaml`
- Modify: `CLAUDE.md`
- Create: `.yamllint.yml`
- Create: `scripts/check-json.py`

- [ ] **Step 1: Add `output-format = "concise"` to pyproject.toml**

In `pyproject.toml`, add to the existing `[tool.ruff]` section:

```toml
[tool.ruff]
target-version = "py312"
line-length = 140
output-format = "concise"
```

- [ ] **Step 2: Create `.yamllint.yml`**

```yaml
extends: default

ignore: |
  node_modules/
  .venv/
  .dmux/
  data/
  tests/collectors/cassettes/
  .git/

rules:
  line-length:
    max: 200
  truthy:
    check-keys: false
  document-start: disable
```

- [ ] **Step 3: Create `scripts/check-json.py`**

```python
"""Validate all JSON files in the project."""
from __future__ import annotations

import json
import pathlib
import sys

EXCLUDES = {"node_modules", ".venv", ".dmux", "data", ".git"}

errors = []
for p in pathlib.Path(".").rglob("*.json"):
    if any(part in EXCLUDES for part in p.parts):
        continue
    try:
        json.loads(p.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"{p}: {e}")

if errors:
    for e in errors:
        print(e, file=sys.stderr)
    sys.exit(1)
```

- [ ] **Step 4: Update Makefile**

Replace the existing `lint:` target and add `fix:`:

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

Keep all other targets unchanged.

- [ ] **Step 5: Update `.pre-commit-config.yaml`**

Replace entire contents:

```yaml
# Pre-commit hooks configuration
# Install: brew install prek && prek install (or: pip install pre-commit && pre-commit install)
repos:
  - repo: local
    hooks:
      - id: fix
        name: auto-fix (ruff)
        entry: make fix
        language: system
        pass_filenames: false
        always_run: true

      - id: lint
        name: lint (ruff + ty + yamllint + json)
        entry: make lint
        language: system
        pass_filenames: false
        always_run: true
```

- [ ] **Step 6: Update CLAUDE.md dev commands section**

Replace the existing `Lint:` line in the Dev Commands section:

```
- Lint: `make lint` (check only, never modifies files — safe for AI to run anytime)
- Fix: `make fix` (auto-fix formatting and import sorting — modifies files)
```

- [ ] **Step 7: Verify `make lint` passes**

```bash
make lint
```

Expected: Clean output. If yamllint or check-json find issues, fix them.

- [ ] **Step 8: Verify `make fix` works**

```bash
make fix
```

Expected: No errors. Files may be reformatted silently.

- [ ] **Step 9: Commit**

```bash
git add Makefile pyproject.toml .yamllint.yml scripts/check-json.py .pre-commit-config.yaml CLAUDE.md
git commit -m "feat: restructure lint infrastructure — AI-safe make lint, add yamllint and JSON validation"
```

---

## Task 3: RSS Pydantic Bug Fix

**Files:**
- Modify: `src/aggre/workflows/rss_collection.py:25-27`

- [ ] **Step 1: Verify ty catches the bug**

```bash
uv run ty check src/aggre/workflows/rss_collection.py
```

Expected: 2 errors about `invalid-argument-type` on line 27.

- [ ] **Step 2: Fix the bug**

In `src/aggre/workflows/rss_collection.py`, replace lines 25-27:

```python
        result = collect_source(engine, cfg, "rss", RssCollector, source_config=single_config, hatchet=h)
        ctx.log(f"Collected {result.succeeded} from {input.name}")
        return result
```

- [ ] **Step 3: Verify ty passes**

```bash
uv run ty check src/aggre/workflows/rss_collection.py
```

Expected: "All checks passed!"

- [ ] **Step 4: Run `make lint`**

```bash
make lint
```

Expected: Clean.

- [ ] **Step 5: Commit**

```bash
git add src/aggre/workflows/rss_collection.py
git commit -m "fix: RSS collection pydantic error — return CollectResult directly instead of wrapping"
```

---

## Task 4: HN Collection Widening

**Files:**
- Modify: `src/aggre/collectors/hackernews/config.py:13` (fetch_limit default)
- Modify: `src/aggre/collectors/hackernews/collector.py:55` (remove front_page tag)
- Modify: `tests/collectors/test_hackernews.py` (update test expectations)

- [ ] **Step 1: Write test for wider collection**

In `tests/collectors/test_hackernews.py`, add to `TestHackernewsCollectorDiscussions`:

```python
    def test_fetches_all_stories_not_just_front_page(self, engine, mock_http):
        """Collector uses tags=story (not story,front_page) to catch all stories."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit()
        route = mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        # Verify the query used tags=story (not story,front_page)
        request = route.calls[0].request
        assert "tags=story" in str(request.url)
        assert "front_page" not in str(request.url)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
make test-e2e 2>&1 | grep -A 2 "test_fetches_all_stories_not_just_front_page"
```

Expected: FAIL — current code sends `tags=story,front_page`.

- [ ] **Step 3: Update config default**

In `src/aggre/collectors/hackernews/config.py`, change:

```python
class HackernewsConfig(BaseModel):
    fetch_limit: int = 1000
    init_fetch_limit: int = 200
    sources: list[HackernewsSource] = []
```

- [ ] **Step 4: Remove `front_page` from Algolia query**

In `src/aggre/collectors/hackernews/collector.py`, change the params dict (around line 54-58):

```python
                    resp = client.get(
                        f"{HN_ALGOLIA_BASE}/search_by_date",
                        params={
                            "tags": "story",
                            "hitsPerPage": config.fetch_limit,
                        },
                    )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
make test-e2e 2>&1 | grep -A 2 "test_fetches_all_stories_not_just_front_page"
```

Expected: PASS.

- [ ] **Step 6: Run all HN tests**

```bash
make test-e2e 2>&1 | grep -E "(test_hackernews|PASSED|FAILED|ERROR)"
```

Expected: All HN tests pass. Some mock URLs may need adjustment if they match on `front_page`.

- [ ] **Step 7: Run `make lint`**

```bash
make lint
```

Expected: Clean.

- [ ] **Step 8: Commit**

```bash
git add src/aggre/collectors/hackernews/config.py src/aggre/collectors/hackernews/collector.py tests/collectors/test_hackernews.py
git commit -m "feat: widen HN collection — fetch all stories, not just front page (1000 items/hour)"
```

---

## Task 5: Lobsters Collection Widening

**Files:**
- Modify: `src/aggre/collectors/lobsters/config.py` (add `pages` field)
- Modify: `src/aggre/collectors/lobsters/collector.py` (paginate URLs)
- Modify: `tests/collectors/test_lobsters.py` (update tests)

- [ ] **Step 1: Write test for pagination**

In `tests/collectors/test_lobsters.py`, add to `TestLobstersCollectorDiscussions`:

```python
    def test_paginates_multiple_pages(self, engine, mock_http):
        """Collector fetches multiple pages when config.pages > 1."""
        story_p1 = lobsters_story(short_id="page1")
        story_p2 = lobsters_story(short_id="page2")

        mock_http.get(url__regex=r"hottest\.json\?page=1").respond(json=[story_p1])
        mock_http.get(url__regex=r"hottest\.json\?page=2").respond(json=[story_p2])
        mock_http.get(url__regex=r"newest\.json\?page=1").respond(json=[])
        mock_http.get(url__regex=r"newest\.json\?page=2").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=2))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 2

    def test_tag_urls_paginated(self, engine, mock_http):
        """Tag URLs are also paginated."""
        story = lobsters_story()

        mock_http.get(url__regex=r"t/rust\.json\?page=1").respond(json=[story])
        mock_http.get(url__regex=r"t/rust\.json\?page=2").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters", tags=["rust"])], pages=2))
            count = collect(LobstersCollector(), engine, config.lobsters, config.settings)

        assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
make test-e2e 2>&1 | grep -A 2 "test_paginates_multiple_pages\|test_tag_urls_paginated"
```

Expected: FAIL — `pages` is not a valid field yet, and no pagination logic exists.

- [ ] **Step 3: Add `pages` to config**

In `src/aggre/collectors/lobsters/config.py`:

```python
class LobstersConfig(BaseModel):
    fetch_limit: int = 50
    init_fetch_limit: int = 200
    pages: int = 4
    sources: list[LobstersSource] = []
```

- [ ] **Step 4: Implement pagination in collector**

In `src/aggre/collectors/lobsters/collector.py`, update `collect_discussions` to paginate. Replace the URL building and fetching loop (approximately lines 52-74):

```python
                urls: list[str] = []
                if lob_source.tags:
                    for tag in lob_source.tags:
                        for page in range(1, config.pages + 1):
                            urls.append(f"{LOBSTERS_BASE}/t/{tag}.json?page={page}")
                else:
                    for page in range(1, config.pages + 1):
                        urls.append(f"{LOBSTERS_BASE}/hottest.json?page={page}")
                        urls.append(f"{LOBSTERS_BASE}/newest.json?page={page}")
```

The rest of the loop (fetching each URL, deduplicating by `stories_by_id`) stays the same.

- [ ] **Step 5: Run new tests to verify they pass**

```bash
make test-e2e 2>&1 | grep -A 2 "test_paginates_multiple_pages\|test_tag_urls_paginated"
```

Expected: PASS.

- [ ] **Step 6: Update existing tests to use `pages=1`**

Existing Lobsters tests were written without pagination. With `pages=4` default, they'd call 8 URLs instead of 2 and may break mock expectations. Add `pages=1` to all existing test configs to preserve their focused intent.

In `tests/collectors/test_lobsters.py`, update every `make_config(lobsters=LobstersConfig(...))` call in existing tests (NOT the new pagination tests) to include `pages=1`:

```python
config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")], pages=1))
```

This applies to: `test_stores_posts`, `test_dedup_across_runs`, `test_multiple_stories`, `test_tag_filtering`, `test_no_config_returns_zero`.

Also in `TestLobstersSource`: `test_creates_source_row`, `test_reuses_existing_source`.

- [ ] **Step 7: Run all Lobsters tests**

```bash
make test-e2e 2>&1 | grep -E "(test_lobsters|PASSED|FAILED|ERROR)"
```

Expected: All Lobsters tests pass.

- [ ] **Step 8: Run `make lint`**

```bash
make lint
```

- [ ] **Step 9: Commit**

```bash
git add src/aggre/collectors/lobsters/config.py src/aggre/collectors/lobsters/collector.py tests/collectors/test_lobsters.py
git commit -m "feat: widen Lobsters collection — paginate hottest/newest/tag endpoints (200 items/cycle)"
```

---

## Task 6: Remove Discussion Search Code

**Files:**
- Modify: `src/aggre/collectors/hackernews/collector.py` (delete `search_by_url`)
- Modify: `src/aggre/collectors/lobsters/collector.py` (delete `search_by_url`, `_domain_cache`, `__init__`)
- Modify: `src/aggre/collectors/base.py` (delete `SearchableCollector`)
- Modify: `tests/collectors/test_hackernews.py` (delete `TestHackernewsSearchByUrl`)
- Modify: `tests/collectors/test_lobsters.py` (delete `TestLobstersSearchByUrl`)
- Modify: `docs/guidelines/semantic-model.md` (remove stale references)
- Modify: `.planning/codebase/STRUCTURE.md` (remove stale references)
- Modify: `.planning/codebase/INTEGRATIONS.md` (remove stale references)
- Modify: `.planning/codebase/TESTING.md` (remove stale references)

- [ ] **Step 1: Delete `SearchableCollector` protocol from base.py**

In `src/aggre/collectors/base.py`, delete the `SearchableCollector` class (lines 53-58):

```python
class SearchableCollector(Collector, Protocol):
    """Collector that supports searching for discussions by URL."""

    def search_by_url(self, url: str, engine: sa.engine.Engine, config: BaseModel, settings: Settings) -> int:
        """Search for discussions about a URL. Returns count of new items stored."""
        ...
```

- [ ] **Step 2: Delete `search_by_url` from HN collector**

In `src/aggre/collectors/hackernews/collector.py`, delete the entire `search_by_url` method (lines 154-193).

- [ ] **Step 3: Delete `search_by_url`, `__init__`, and `_domain_cache` from Lobsters collector**

In `src/aggre/collectors/lobsters/collector.py`:
- Delete `__init__` method (lines 32-33) and the `_domain_cache` field
- Delete `search_by_url` method (lines 151-204)
- Remove `from urllib.parse import urlparse` import (only used by `search_by_url`)

- [ ] **Step 4: Delete test classes**

In `tests/collectors/test_hackernews.py`, delete the entire `TestHackernewsSearchByUrl` class (lines 193-265).

In `tests/collectors/test_lobsters.py`, delete the entire `TestLobstersSearchByUrl` class (lines 125-201). Also remove the `logging` import (line 6) if it's only used by that class.

- [ ] **Step 5: Clean up doc references**

In `docs/guidelines/semantic-model.md`:
- Search for and remove `discussions_searched_at` column reference
- Search for and remove `idx_content_needs_discussion_search` index definition
- Search for and remove "Discussion search coverage" query recipe section

In `.planning/codebase/STRUCTURE.md`:
- Search for and remove reference to `src/aggre/dagster_defs/discussion_search/`

In `.planning/codebase/INTEGRATIONS.md`:
- Search for and update or remove the "Enrichment" section referencing discussion search

In `.planning/codebase/TESTING.md`:
- Search for and remove reference to `test_discussion_search.py`

In `.planning/codebase/CONVENTIONS.md`:
- Search for and remove any references to `SearchableCollector`

In `.planning/codebase/ARCHITECTURE.md`:
- Search for and remove any references to discussion search workflow or `SearchableCollector`

- [ ] **Step 6: Run all tests**

```bash
make test-e2e
```

Expected: All tests pass. No test should reference `search_by_url` anymore.

- [ ] **Step 7: Run `make lint`**

```bash
make lint
```

Expected: Clean. Check for unused imports left behind by deleted code.

- [ ] **Step 8: Commit**

```bash
git add src/aggre/collectors/base.py src/aggre/collectors/hackernews/collector.py src/aggre/collectors/lobsters/collector.py tests/collectors/test_hackernews.py tests/collectors/test_lobsters.py docs/guidelines/semantic-model.md .planning/codebase/STRUCTURE.md .planning/codebase/INTEGRATIONS.md .planning/codebase/TESTING.md .planning/codebase/CONVENTIONS.md .planning/codebase/ARCHITECTURE.md
git commit -m "refactor: remove discussion search code — replaced by wider collection + content_id URL matching"
```

---

## Task 7: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
make test-e2e
```

Expected: All tests pass.

- [ ] **Step 2: Run full lint**

```bash
make lint
```

Expected: Clean.

- [ ] **Step 3: Verify ty passes**

```bash
uv run ty check src tests
```

Expected: "All checks passed!"

- [ ] **Step 4: Verify make fix works**

```bash
make fix
```

Expected: No errors.

- [ ] **Step 5: Check git status is clean**

```bash
git status
git diff
```

Expected: No uncommitted changes (make fix should find nothing to fix if make lint already passes).
