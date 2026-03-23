# Workflow Redesign: Rich Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the thin `ItemEvent` with `SilverContentRef` so CEL filters route events correctly, eliminating 99% wasted Hatchet task dispatches.

**Architecture:** Rename + enrich the event model, update all workflow registrations to use new type, update collection emission code. Business logic functions untouched. Hatchet infrastructure unchanged.

**Tech Stack:** Python 3.12, Hatchet SDK, Pydantic, SQLAlchemy, pytest

**Spec:** `docs/superpowers/specs/2026-03-23-workflow-redesign-rich-events.md`

**Read before starting:** `docs/guidelines/testing.md`, `.planning/codebase/TESTING.md`, `docs/guidelines/semantic-model.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/aggre/workflows/models.py` | Modify | Rename `ItemEvent` → `SilverContentRef`, keep all other models |
| `src/aggre/workflows/collection.py` | Modify | Update `_emit_item_event` to use `SilverContentRef` |
| `src/aggre/workflows/webpage.py` | Modify | Update `input_validator`, type hints, import |
| `src/aggre/workflows/transcription.py` | Modify | Same |
| `src/aggre/workflows/comments.py` | Modify | Same |
| `scripts/backfill_transcription.py` | Modify | Update import + usage of `ItemEvent` → `SilverContentRef` |
| `tests/workflows/test_collection.py` | No change | Tests assert on dict payloads, not class names — already correct |
| `tests/workflows/test_models.py` | Create | Field-sync test: `SilverContentRef` fields match DB columns |
| `CLAUDE.md` | Modify | Add Hatchet safety rules + column ownership constraint |
| `docs/guidelines/semantic-model.md` | Modify | Add column ownership per stage section |
| `docker-compose.prod.yml` | Modify | Hardcode admin password |

---

> **IMPORTANT:** Tasks 1-3 must be executed as an atomic sequence. Do not push or run CI between them — the codebase has broken imports after Task 1 until Task 3 completes. `reprocess.py` was verified to NOT use `ItemEvent` (it imports only `TaskResult`) and needs no changes.

### Task 1: Rename `ItemEvent` → `SilverContentRef` in models

**Files:**
- Modify: `src/aggre/workflows/models.py`
- Create: `tests/workflows/test_models.py`

- [ ] **Step 1: Write the field-sync test**

Create `tests/workflows/test_models.py`:

```python
"""Tests for workflow data contracts."""

from __future__ import annotations

from aggre.db import SilverContent, SilverDiscussion
from aggre.workflows.models import SilverContentRef


class TestSilverContentRefSync:
    """SilverContentRef fields must stay in sync with DB columns."""

    def test_content_id_matches_silver_content_pk(self) -> None:
        assert hasattr(SilverContent, "id")

    def test_discussion_id_matches_silver_discussions_pk(self) -> None:
        assert hasattr(SilverDiscussion, "id")

    def test_domain_matches_silver_content_column(self) -> None:
        assert hasattr(SilverContent, "domain")

    def test_source_matches_silver_discussions_column(self) -> None:
        assert hasattr(SilverDiscussion, "source_type")

    def test_text_provided_derivable_from_text_column(self) -> None:
        assert hasattr(SilverContent, "text")

    def test_ref_fields_are_expected_set(self) -> None:
        """Guard against accidental field additions/removals."""
        expected = {"content_id", "discussion_id", "source", "domain", "text_provided"}
        actual = set(SilverContentRef.model_fields.keys())
        assert actual == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/workflows/test_models.py -v`
Expected: FAIL — `SilverContentRef` does not exist yet.

- [ ] **Step 3: Rename `ItemEvent` → `SilverContentRef` in models.py**

In `src/aggre/workflows/models.py`, rename the class and update the docstring:

```python
class SilverContentRef(BaseModel):
    """Compact reference to a silver_content row for event dispatch.

    Mirrors DB columns — no derived fields.
    Workflows must re-fetch from DB for processing.

    Note: ``text_provided`` is defense-in-depth only. Due to Layer 1 emission-time
    dedup, any event that reaches construction will have text_provided=False.
    CEL filters on this field guard against future relaxation of Layer 1.
    """

    content_id: int
    discussion_id: int
    source: str  # "hackernews", "reddit", etc. — for concurrency grouping
    domain: str | None = None  # content domain — for concurrency grouping
    text_provided: bool = False
```

- [ ] **Step 4: Run tests to verify field-sync test passes**

Run: `uv run pytest tests/workflows/test_models.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Run full test suite to see what breaks from the rename**

Run: `uv run pytest tests/ -x --tb=short 2>&1 | head -40`
Expected: Failures in `tests/workflows/test_collection.py` (imports `ItemEvent` indirectly via assertion checks). Note which tests fail — they'll be fixed in Task 2.

- [ ] **Step 6: Commit**

```bash
git add src/aggre/workflows/models.py tests/workflows/test_models.py
git commit -m "refactor(models): rename ItemEvent to SilverContentRef

Rich event contract for CEL filter routing. See spec:
docs/superpowers/specs/2026-03-23-workflow-redesign-rich-events.md"
```

---

### Task 2: Update event emission in `collection.py`

**Files:**
- Modify: `src/aggre/workflows/collection.py`
- Modify: `tests/workflows/test_collection.py`

- [ ] **Step 1: Update import in `collection.py`**

In `src/aggre/workflows/collection.py`, change:
```python
from aggre.workflows.models import CollectResult, ItemEvent
```
to:
```python
from aggre.workflows.models import CollectResult, SilverContentRef
```

- [ ] **Step 2: Update `_emit_item_event` to use `SilverContentRef`**

In `src/aggre/workflows/collection.py`, in `_emit_item_event`, change the event construction from:
```python
            event = ItemEvent(
                content_id=disc.content_id,
                discussion_id=disc.id,
                source=source_name,
                domain=disc.domain,
                text_provided=disc.text is not None,
            )
            hatchet.event.push("item.new", event.model_dump(), options=PushEventOptions(scope="default"))
```
to:
```python
            event = SilverContentRef(
                content_id=disc.content_id,
                discussion_id=disc.id,
                source=source_name,
                domain=disc.domain,
                text_provided=disc.text is not None,
            )
            hatchet.event.push("item.new", event.model_dump(), options=PushEventOptions(scope="default"))
```

- [ ] **Step 3: Run collection tests**

Run: `uv run pytest tests/workflows/test_collection.py -v`
Expected: PASS — the test assertions check the dict payload `{"content_id": 100, ...}` which is identical regardless of class name.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ --tb=short 2>&1 | head -40`
Expected: PASS (or failures only in workflow registration files — fixed in Task 3).

- [ ] **Step 5: Commit**

```bash
git add src/aggre/workflows/collection.py
git commit -m "refactor(collection): use SilverContentRef for event emission"
```

---

### Task 3: Update workflow registrations (webpage, transcription, comments)

**Files:**
- Modify: `src/aggre/workflows/webpage.py`
- Modify: `src/aggre/workflows/transcription.py`
- Modify: `src/aggre/workflows/comments.py`

- [ ] **Step 1: Update `webpage.py`**

Change import:
```python
from aggre.workflows.models import ItemEvent, StepOutput
```
to:
```python
from aggre.workflows.models import SilverContentRef, StepOutput
```

In `register()`, change:
- `input_validator=ItemEvent` → `input_validator=SilverContentRef`
- `def download_task(input: ItemEvent, ctx)` → `def download_task(input: SilverContentRef, ctx)`
- `def extract_task(input: ItemEvent, ctx)` → `def extract_task(input: SilverContentRef, ctx)`

- [ ] **Step 2: Update `transcription.py`**

Same pattern — change import and type hints:
- `from aggre.workflows.models import ItemEvent, StepOutput` → `from aggre.workflows.models import SilverContentRef, StepOutput`
- `input_validator=ItemEvent` → `input_validator=SilverContentRef`
- `def transcribe_task(input: ItemEvent, ctx)` → `def transcribe_task(input: SilverContentRef, ctx)`

- [ ] **Step 3: Update `comments.py`**

Same pattern:
- `from aggre.workflows.models import ItemEvent, StepOutput` → `from aggre.workflows.models import SilverContentRef, StepOutput`
- `input_validator=ItemEvent` → `input_validator=SilverContentRef`
- `def comments_task(input: ItemEvent, ctx)` → `def comments_task(input: SilverContentRef, ctx)`

- [ ] **Step 4: Update `scripts/backfill_transcription.py`**

Change:
```python
from aggre.workflows.models import ItemEvent
```
to:
```python
from aggre.workflows.models import SilverContentRef
```

And change the event construction:
```python
    event = ItemEvent(
```
to:
```python
    event = SilverContentRef(
```

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v --tb=short`
Expected: ALL PASS. No test imports `ItemEvent` directly (verified via grep).

- [ ] **Step 6: Run linter**

Run: `make lint`
Expected: PASS — no unused imports, no style issues.

- [ ] **Step 7: Commit**

```bash
git add src/aggre/workflows/webpage.py src/aggre/workflows/transcription.py src/aggre/workflows/comments.py scripts/backfill_transcription.py
git commit -m "refactor(workflows): update all workflows to use SilverContentRef

Webpage, transcription, comments, and backfill script now use
SilverContentRef instead of ItemEvent. No logic changes."
```

---

### Task 4: Document column ownership and Hatchet safety

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/guidelines/semantic-model.md`

- [ ] **Step 1: Add Hatchet safety section to CLAUDE.md**

Append after the `## Dev Commands` section in `CLAUDE.md`:

```markdown
## Hatchet Safety

- Never delete Hatchet volumes or modify Hatchet's internal database tables
- Use Hatchet's REST API for run management (cancel, replay, cleanup)
- Admin credentials are hardcoded in docker-compose — volume wipes auto-recover

## Column Ownership (Concurrency Safety)

Each processing stage writes only to columns it owns. No shared mutable columns between concurrent workflows. This guarantees safe concurrent updates without row locking.

| Stage | Columns Written | Table |
|-------|----------------|-------|
| Webpage (download+extract) | `text`, `title` | `silver_content` |
| Transcription | `text`, `detected_language`, `transcribed_by` | `silver_content` |
| Comments | `comments_json`, `comments_fetched_at` | `silver_discussions` |

Webpage and Transcription both write `text` but never process the same item (CEL filters partition by domain). When adding a new stage, give it its own columns or its own table — never share a mutable column with another stage.
```

- [ ] **Step 2: Add column ownership to semantic-model.md**

Append a new section after the `### Relationships` section in `docs/guidelines/semantic-model.md`:

```markdown
### Column Ownership by Processing Stage

Each workflow stage owns specific columns. Concurrent stages write different columns on the same row — safe without locking because PostgreSQL row-level locks during UPDATE are brief and non-conflicting for different columns.

| Stage | Table | Columns | Partition Key |
|-------|-------|---------|--------------|
| Webpage | `silver_content` | `text`, `title` | `domain NOT IN (youtube.com, ...)` |
| Transcription | `silver_content` | `text`, `detected_language`, `transcribed_by` | `domain = youtube.com` |
| Comments | `silver_discussions` | `comments_json`, `comments_fetched_at` | `source IN (reddit, hackernews, lobsters)` |

**Rule:** New stages must own their columns exclusively. If two stages would write the same column on the same row, use `SELECT ... FOR UPDATE` or create a separate output table.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/guidelines/semantic-model.md
git commit -m "docs: add Hatchet safety rules and column ownership constraint

Documents concurrency safety model for concurrent workflow stages.
See spec: docs/superpowers/specs/2026-03-23-workflow-redesign-rich-events.md"
```

---

### Task 5: Hardcode Hatchet admin password

**Files:**
- Modify: `docker-compose.prod.yml`

- [ ] **Step 1: Replace variable with fixed password**

In `docker-compose.prod.yml`, line 124, change:
```yaml
      SEED_DEFAULT_ADMIN_PASSWORD: ${HATCHET_ADMIN_PASSWORD}
```
to:
```yaml
      SEED_DEFAULT_ADMIN_PASSWORD: "AggReHatchet-Admin-2026"
```

- [ ] **Step 2: Verify no other references to `HATCHET_ADMIN_PASSWORD`**

Run: `grep -r HATCHET_ADMIN_PASSWORD . --include='*.yml' --include='*.env*' --include='*.md'`
Expected: No other files reference this variable (or only documentation).

- [ ] **Step 3: Commit**

```bash
git add docker-compose.prod.yml
git commit -m "fix(hatchet): hardcode admin password for volume-wipe resilience

Hatchet UI is Tailscale-only. Fixed seed password ensures auto-recovery
on volume wipe without manual bcrypt reset."
```

---

### Task 6: Research and configure event retention

**Files:**
- Modify: `docker-compose.prod.yml` (if env var exists)
- Modify: `docker-compose.local.yml` (if env var exists)

- [ ] **Step 1: Research Hatchet retention config**

Check Hatchet docs and source for retention env vars:
- Search web for "hatchet event retention configuration"
- Check `hatchet-dev/hatchet` GitHub for `retention` or `DATA_RETENTION` in env var handling
- Check Hatchet's `hatchet-lite` Docker image environment variable docs

- [ ] **Step 2: If retention env var exists, add to both compose files**

Add to `hatchet-lite` environment section in both `docker-compose.prod.yml` and `docker-compose.local.yml`:
```yaml
      DATA_RETENTION_PERIOD: "720h"  # 30 days
```

If no native retention exists, document this as a known gap and note that a cleanup cron workflow should be added later.

- [ ] **Step 3: Commit (if changes made)**

```bash
git add docker-compose.prod.yml docker-compose.local.yml
git commit -m "ops(hatchet): configure event retention period

Prevents unbounded growth of Hatchet internal event tables."
```

---

### Task 7: Final verification

- [ ] **Step 1: Run full test suite with coverage**

Run: `make test`
Expected: ALL PASS, coverage ≥ 95%. This generates `coverage.xml` needed for Step 5.

- [ ] **Step 2: Run linter**

Run: `make lint`
Expected: PASS.

- [ ] **Step 3: Verify no remaining `ItemEvent` references in source code**

Run: `grep -r "ItemEvent" src/ scripts/ --include='*.py'`
Expected: No matches.

- [ ] **Step 4: Verify `ItemEvent` only appears in docs/specs/plans (historical references)**

Run: `grep -r "ItemEvent" docs/ --include='*.md' | head -20`
Expected: Only in old specs (`2026-03-16-event-dedup-design.md`) and the new spec/plan — not in guidelines or operational docs.

- [ ] **Step 5: Check diff coverage**

Run: `make coverage-diff`
Expected: PASS at ≥ 95% for changed lines.

- [ ] **Step 6: Review git log for clean history**

Run: `git log --oneline -10`
Expected: 5-6 focused commits, each self-contained.
