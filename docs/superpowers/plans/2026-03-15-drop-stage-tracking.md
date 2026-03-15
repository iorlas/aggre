# Drop Stage Tracking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `stage_tracking` table and tracking module, replacing them with two timestamps on Silver tables (`discussions_searched_at`, `comments_fetched_at`) and delegating retry/failure logic to Hatchet.

**Architecture:** Add timestamps to existing ORM models, update discussion search workflow to set `discussions_searched_at`, simplify `_mark_comments_done` to set `comments_fetched_at` instead of writing to `stage_tracking`, delete the tracking module and legacy batch comment methods, write a migration to add columns/backfill/drop table.

**Tech Stack:** SQLAlchemy ORM, Alembic migrations, PostgreSQL, Hatchet workflows, pytest

**Spec:** `docs/superpowers/specs/2026-03-15-drop-stage-tracking-design.md`

---

## Chunk 1: ORM Changes and Migration

### Task 1: Add timestamps to ORM models and update index

**Files:**
- Modify: `src/aggre/db.py:25-67`

- [ ] **Step 1: Add `discussions_searched_at` to `SilverContent`**

Add after `transcribed_by` (line 36):

```python
discussions_searched_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
```

- [ ] **Step 2: Add `comments_fetched_at` to `SilverDiscussion`**

Add after `comment_count` (line 57):

```python
comments_fetched_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
```

- [ ] **Step 3: Replace `idx_content_needs_discussion_search` index**

Replace the existing index (lines 63-67):

```python
# Old:
sa.Index(
    "idx_content_needs_discussion_search",
    SilverContent.id,
    postgresql_where=sa.and_(SilverContent.text.isnot(None), SilverContent.canonical_url.isnot(None)),
)

# New:
sa.Index(
    "idx_content_needs_discussion_search",
    SilverContent.id,
    postgresql_where=sa.and_(SilverContent.discussions_searched_at.is_(None), SilverContent.text.isnot(None)),
)
```

- [ ] **Step 4: Verify ORM loads correctly**

Run: `uv run python -c "from aggre.db import SilverContent, SilverDiscussion; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add src/aggre/db.py
git commit -m "Add discussions_searched_at and comments_fetched_at to Silver ORM models"
```

### Task 2: Write Alembic migration

**Files:**
- Create: `alembic/versions/010_drop_stage_tracking.py`

- [ ] **Step 1: Create migration file**

```python
"""Drop stage_tracking, add Silver timestamps.

Revision ID: 010
Revises: 009
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add new columns
    op.add_column("silver_content", sa.Column("discussions_searched_at", sa.Text(), nullable=True))
    op.add_column("silver_discussions", sa.Column("comments_fetched_at", sa.Text(), nullable=True))

    # 2. Replace discussion search index
    op.drop_index("idx_content_needs_discussion_search", table_name="silver_content")
    op.create_index(
        "idx_content_needs_discussion_search",
        "silver_content",
        ["id"],
        postgresql_where=sa.text("discussions_searched_at IS NULL AND text IS NOT NULL"),
    )

    # 3. Backfill comments_fetched_at for already-fetched comments
    op.execute("UPDATE silver_discussions SET comments_fetched_at = now()::text WHERE comments_json IS NOT NULL")

    # 4. Drop stage_tracking table (cascades idx_stage_actionable)
    op.drop_table("stage_tracking")


def downgrade() -> None:
    # Recreate stage_tracking table
    op.create_table(
        "stage_tracking",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retries", sa.Integer(), server_default="0"),
        sa.Column("last_ran_at", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("source", "external_id", "stage"),
    )
    op.create_index(
        "idx_stage_actionable",
        "stage_tracking",
        ["stage"],
        postgresql_where=sa.text("status = 'failed'"),
    )

    # Restore old discussion search index
    op.drop_index("idx_content_needs_discussion_search", table_name="silver_content")
    op.create_index(
        "idx_content_needs_discussion_search",
        "silver_content",
        ["id"],
        postgresql_where=sa.text("text IS NOT NULL AND canonical_url IS NOT NULL"),
    )

    # Drop new columns
    op.drop_column("silver_discussions", "comments_fetched_at")
    op.drop_column("silver_content", "discussions_searched_at")
```

- [ ] **Step 2: Run migration against test DB**

Run: `make test-e2e` (spins up ephemeral postgres, runs all tests including acceptance migration tests)

Note: This will fail because acceptance tests still assert `stage_tracking` exists. That's expected — we'll fix tests in Task 5.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/010_drop_stage_tracking.py
git commit -m "Add migration 010: drop stage_tracking, add Silver timestamps"
```

## Chunk 2: Update Production Code

### Task 3: Update `_mark_comments_done` in base collector

**Files:**
- Modify: `src/aggre/collectors/base.py`

- [ ] **Step 1: Remove tracking imports and simplify `_mark_comments_done`**

Delete the imports (lines 17-19):

```python
# DELETE these lines:
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter, upsert_done, upsert_failed
from aggre.tracking.status import Stage
```

Replace `_mark_comments_done` (lines 136-154) — note: `external_id` parameter removed since `upsert_done` was the only consumer:

```python
def _mark_comments_done(
    self,
    engine: sa.engine.Engine,
    discussion_id: int,
    comments_json: str | None,
    comment_count: int,
) -> None:
    """Store fetched comments on a discussion."""
    with engine.begin() as conn:
        conn.execute(
            sa.update(SilverDiscussion)
            .where(SilverDiscussion.id == discussion_id)
            .values(
                comments_json=comments_json,
                comment_count=comment_count,
                comments_fetched_at=now_iso(),
            )
        )
```

- [ ] **Step 2: Update all `_mark_comments_done` call sites to remove `external_id` argument**

In each collector's `fetch_discussion_comments`, change:

```python
# Old (all three collectors):
self._mark_comments_done(engine, discussion_id, external_id, json.dumps(children), len(children))

# New:
self._mark_comments_done(engine, discussion_id, json.dumps(children), len(children))
```

Files to update:
- `src/aggre/collectors/hackernews/collector.py` line 200
- `src/aggre/collectors/lobsters/collector.py` line 197
- `src/aggre/collectors/reddit/collector.py` line 262

- [ ] **Step 3: Delete `_query_pending_comments` method (lines 112-134)**

This method is only used by deleted `collect_comments()` batch methods.

- [ ] **Step 4: Delete `_mark_comments_failed` method (lines 156-163)**

Hatchet handles failure tracking.

- [ ] **Step 5: Verify imports resolve**

Run: `uv run python -c "from aggre.collectors.base import BaseCollector; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/aggre/collectors/base.py
git commit -m "Remove stage_tracking from base collector, set comments_fetched_at"
```

### Task 4: Delete legacy batch comment methods from collectors + set `discussions_searched_at`

**Files:**
- Modify: `src/aggre/collectors/hackernews/collector.py`
- Modify: `src/aggre/collectors/lobsters/collector.py`
- Modify: `src/aggre/collectors/reddit/collector.py`
- Modify: `src/aggre/workflows/discussion_search.py`

- [ ] **Step 1: Delete `collect_comments()` from HackernewsCollector**

Delete the `collect_comments` method (lines 134-180 in `hackernews/collector.py`). Keep `fetch_discussion_comments` — it's the production code path.

- [ ] **Step 2: Delete `collect_comments()` from LobstersCollector**

Delete lines 131-177 in `lobsters/collector.py`. Keep `fetch_discussion_comments`.

- [ ] **Step 3: Delete `collect_comments()` from RedditCollector**

Delete lines 180-234 in `reddit/collector.py`. Keep `fetch_discussion_comments`.

- [ ] **Step 4: Update `search_one` in discussion_search.py to set `discussions_searched_at`**

Add import at top:

```python
from aggre.db import SilverContent, update_content
```

(Change existing `from aggre.db import SilverContent` to include `update_content`.)

Add import for `now_iso`:

```python
from aggre.utils.db import get_engine, now_iso
```

(Change existing `from aggre.utils.db import get_engine` to include `now_iso`.)

After the successful search (before the return on line 94), add:

```python
    update_content(engine, content_id, discussions_searched_at=now_iso())
```

This goes right before the `return StepOutput(status=status, ...)` line at the end of `search_one`.

- [ ] **Step 5: Verify imports resolve**

Run: `uv run python -c "from aggre.collectors.hackernews.collector import HackernewsCollector; from aggre.collectors.lobsters.collector import LobstersCollector; from aggre.collectors.reddit.collector import RedditCollector; from aggre.workflows.discussion_search import search_one; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/aggre/collectors/hackernews/collector.py src/aggre/collectors/lobsters/collector.py src/aggre/collectors/reddit/collector.py src/aggre/workflows/discussion_search.py
git commit -m "Delete legacy collect_comments, set discussions_searched_at on search"
```

## Chunk 3: Delete Tracking Module and Update Tests

### Task 5: Delete tracking module and remove all tracking imports (atomic)

**IMPORTANT:** These changes must happen together in one commit — deleting the module without removing imports would break the codebase.

**Files:**
- Delete: `src/aggre/tracking/__init__.py`
- Delete: `src/aggre/tracking/model.py`
- Delete: `src/aggre/tracking/ops.py`
- Delete: `src/aggre/tracking/status.py`
- Delete: `tests/tracking/__init__.py`
- Delete: `tests/tracking/test_ops.py`
- Delete: `tests/tracking/test_invariants.py`
- Modify: `tests/conftest.py:14`
- Modify: `tests/helpers.py:8-9,48-87`

- [ ] **Step 1: Remove tracking import from `conftest.py`**

Delete line 14:

```python
import aggre.tracking.model  # noqa: F401 -- register StageTracking with Base.metadata
```

- [ ] **Step 2: Remove tracking helpers from `helpers.py`**

Delete the imports (lines 8-9):

```python
from aggre.tracking.model import StageTracking
from aggre.tracking.status import Stage, StageStatus
```

Delete the `assert_tracking` function (lines 48-69) and `assert_no_tracking` function (lines 72-87). These are unused — no test file imports them.

- [ ] **Step 3: Delete `src/aggre/tracking/` directory**

```bash
rm -rf src/aggre/tracking/
```

- [ ] **Step 4: Delete `tests/tracking/` directory**

```bash
rm -rf tests/tracking/
```

- [ ] **Step 5: Commit**

```bash
git add -A src/aggre/tracking/ tests/tracking/ tests/conftest.py tests/helpers.py
git commit -m "Delete tracking module, tests, and all tracking imports"
```

### Task 6: Update acceptance tests

**Files:**
- Modify: `tests/test_acceptance_cli.py`

- [ ] **Step 1: Update `test_upgrade_head_creates_expected_tables`**

Remove `stage_tracking` from expected tables (line 63):

```python
# DELETE this line:
assert "stage_tracking" in table_names
```

Add `stage_tracking` to "Tables that should NOT exist" section (after line 69):

```python
assert "stage_tracking" not in table_names
```

Remove stage_tracking index assertions (lines 87-89):

```python
# DELETE these lines:
# Verify indexes on stage_tracking
st_indexes = {idx["name"] for idx in inspector.get_indexes("stage_tracking")}
assert "idx_stage_actionable" in st_indexes
```

- [ ] **Step 2: Update `test_downgrade_removes_tables`**

The existing assertion `assert "stage_tracking" not in table_names` (line 115) is already correct for the downgrade case — after downgrading to base, no tables exist. Keep as-is.

- [ ] **Step 3: Commit**

```bash
git add tests/test_acceptance_cli.py
git commit -m "Update acceptance tests: stage_tracking no longer exists"
```

### Task 7: Update collector tests — remove `collect_comments` tests, add `comments_fetched_at` assertions

**Files:**
- Modify: `tests/collectors/test_hackernews.py`
- Modify: `tests/collectors/test_lobsters.py`
- Modify: `tests/collectors/test_reddit.py`

- [ ] **Step 1: Delete `collect_comments` tests from `test_hackernews.py`**

Delete the entire test class or methods that test `collect_comments`. These are the tests that call `collector.collect_comments(...)`. Find them by searching for `collect_comments` in the file and remove all test methods that call it. Keep all `fetch_discussion_comments` tests.

- [ ] **Step 2: Delete `collect_comments` tests from `test_lobsters.py`**

Same as above — remove all tests calling `collector.collect_comments(...)`.

- [ ] **Step 3: Delete `collect_comments` tests from `test_reddit.py`**

Same as above — remove all tests calling `RedditCollector().collect_comments(...)`.

- [ ] **Step 4: Add `comments_fetched_at` assertions to existing `fetch_discussion_comments` tests**

In each collector test file, find the test(s) that call `fetch_discussion_comments` and add an assertion that `comments_fetched_at` is set on the discussion row after a successful fetch. Example pattern:

```python
# After the fetch_discussion_comments call:
with engine.connect() as conn:
    row = conn.execute(
        sa.select(SilverDiscussion.comments_fetched_at).where(SilverDiscussion.id == discussion_id)
    ).first()
assert row.comments_fetched_at is not None
```

Add `from aggre.db import SilverDiscussion` and `import sqlalchemy as sa` to each test file if not already imported.

- [ ] **Step 5: Commit**

```bash
git add tests/collectors/test_hackernews.py tests/collectors/test_lobsters.py tests/collectors/test_reddit.py
git commit -m "Update collector tests: remove collect_comments, add comments_fetched_at assertions"
```

### Task 8: Add test for `discussions_searched_at` being set

**Files:**
- Modify: `tests/workflows/test_discussion_search.py`

- [ ] **Step 1: Add test that `discussions_searched_at` is set on successful search**

Add to `TestSearchOne` class:

```python
def test_sets_discussions_searched_at_on_success(self, engine):
    config = make_config()
    content_id = seed_content(engine, "https://example.com/timestamp-test", domain="example.com")

    mock_hn = MagicMock()
    mock_hn.search_by_url.return_value = 0
    mock_lob = MagicMock()
    mock_lob.search_by_url.return_value = 0

    search_one(engine, config, content_id, hn_collector=mock_hn, lobsters_collector=mock_lob)

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(SilverContent.discussions_searched_at).where(SilverContent.id == content_id)
        ).first()
    assert row.discussions_searched_at is not None
```

Add import at top of file:

```python
import sqlalchemy as sa
from aggre.db import SilverContent
```

- [ ] **Step 2: Add test that `discussions_searched_at` is set on partial success**

```python
def test_sets_discussions_searched_at_on_partial_success(self, engine):
    config = make_config()
    content_id = seed_content(engine, "https://example.com/partial-ts", domain="example.com")

    mock_hn = MagicMock()
    mock_hn.search_by_url.side_effect = Exception("HN down")
    mock_lob = MagicMock()
    mock_lob.search_by_url.return_value = 1

    search_one(engine, config, content_id, hn_collector=mock_hn, lobsters_collector=mock_lob)

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(SilverContent.discussions_searched_at).where(SilverContent.id == content_id)
        ).first()
    assert row.discussions_searched_at is not None
```

- [ ] **Step 3: Add test that `discussions_searched_at` is NOT set when both fail**

```python
def test_no_discussions_searched_at_when_both_fail(self, engine):
    config = make_config()
    content_id = seed_content(engine, "https://example.com/both-fail-ts", domain="example.com")

    mock_hn = MagicMock()
    mock_hn.search_by_url.side_effect = Exception("HN down")
    mock_lob = MagicMock()
    mock_lob.search_by_url.side_effect = Exception("Lobsters down")

    with pytest.raises(Exception):
        search_one(engine, config, content_id, hn_collector=mock_hn, lobsters_collector=mock_lob)

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(SilverContent.discussions_searched_at).where(SilverContent.id == content_id)
        ).first()
    assert row.discussions_searched_at is None
```

- [ ] **Step 4: Commit**

```bash
git add tests/workflows/test_discussion_search.py
git commit -m "Add tests for discussions_searched_at timestamp"
```

### Task 9: Run full test suite and fix any remaining issues

- [ ] **Step 1: Run full test suite**

Run: `make test-e2e`

Expected: All tests pass. If any test still imports from `aggre.tracking`, find and fix it.

- [ ] **Step 2: Run linter**

Run: `make lint`

Expected: Clean. If any unused imports remain, fix them.

- [ ] **Step 3: Check diff coverage**

Run: `make coverage-diff`

Expected: Coverage meets 95% threshold on changed lines.

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "Fix remaining test/lint issues from stage_tracking removal"
```

## Chunk 4: Update Documentation

### Task 10: Update semantic model docs

**Files:**
- Modify: `docs/guidelines/semantic-model.md`

- [ ] **Step 1: Add `discussions_searched_at` to `silver_content` schema**

Add to the `silver_content.columns` section:

```yaml
discussions_searched_at: { type: text, nullable: true, description: "ISO 8601 — when discussion search was last run for this URL. NULL = never searched." }
```

- [ ] **Step 2: Add `comments_fetched_at` to `silver_discussions` schema**

Add to the `silver_discussions.columns` section:

```yaml
comments_fetched_at: { type: text, nullable: true, description: "ISO 8601 — when comments were last fetched. NULL = not yet fetched. Used for staleness-based re-fetching." }
```

- [ ] **Step 3: Update index definitions**

Update `idx_content_needs_discussion_search` description:

```yaml
- idx_content_needs_discussion_search: { columns: [id], where: "discussions_searched_at IS NULL AND text IS NOT NULL" }
```

- [ ] **Step 4: Remove `stage_tracking` references**

Remove the `stage_tracking` table definition if present. Remove any references to stage tracking in query recipes or operational sections.

Also remove the stale `error` and `fetched_at` column references from the `silver_content` schema if they're still there — those columns don't exist in the ORM.

- [ ] **Step 5: Update the "Content processing status" query recipe**

The null-check query recipe should reflect that `discussions_searched_at IS NULL` is the way to check for unsearched content, not `enriched_at`.

- [ ] **Step 6: Commit**

```bash
git add docs/guidelines/semantic-model.md
git commit -m "Update semantic model: add Silver timestamps, remove stage_tracking"
```
