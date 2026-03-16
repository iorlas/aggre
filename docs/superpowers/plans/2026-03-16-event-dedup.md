# Event Deduplication Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop collectors from flooding the Hatchet queue with redundant `item.new` events for already-processed content.

**Architecture:** Two-layer dedup. Layer 1: emission-time check in `_emit_item_event` skips events for fully-processed items (`text IS NOT NULL AND discussions_searched_at IS NOT NULL`). Layer 2: `CANCEL_NEWEST` concurrency expression per `content_id` on all 4 event-driven workflows instantly cancels duplicate in-flight runs.

**Tech Stack:** Python 3.12, Hatchet SDK 1.28.1, SQLAlchemy, pytest

**Spec:** `docs/superpowers/specs/2026-03-16-event-dedup-design.md`

---

## Chunk 1: Emission-time dedup check

### Task 1: Add emission-time dedup check to `_emit_item_event`

**Files:**
- Modify: `src/aggre/workflows/collection.py:69-102`
- Test: `tests/workflows/test_collection.py`

**Read first:** `docs/guidelines/testing.md`, `docs/guidelines/semantic-model.md`

- [ ] **Step 1: Write failing test — skips event for fully-processed item**

Add to `tests/workflows/test_collection.py` in the `TestEventEmission` class:

```python
def test_no_event_when_fully_processed(self) -> None:
    """No event emitted when content has text AND discussions_searched_at (fully processed)."""
    cfg = make_config()
    mock_cls = MagicMock()
    mock_cls.return_value.collect_discussions.return_value = [
        {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "ext1"},
    ]

    mock_hatchet = MagicMock()

    engine = MagicMock()
    mock_disc_row = MagicMock()
    mock_disc_row.id = 42
    mock_disc_row.content_id = 100
    mock_disc_row.domain = "example.com"
    mock_disc_row.text = "Some article text"
    mock_disc_row.discussions_searched_at = "2026-03-16T00:00:00+00:00"
    mock_result = MagicMock()
    mock_result.first.return_value = mock_disc_row
    connect_mock = MagicMock()
    connect_mock.execute.return_value = mock_result
    engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)

    mock_hatchet.event.push.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/workflows/test_collection.py::TestEventEmission::test_no_event_when_fully_processed -v`
Expected: FAIL — event is currently emitted regardless of processing state.

- [ ] **Step 3: Write failing test — still emits for self-posts (text set but not searched)**

```python
def test_emits_event_for_self_post(self) -> None:
    """Self-posts have text pre-populated by collector but discussions_searched_at=None.
    Event must still be emitted so discussion-search and comments run."""
    cfg = make_config()
    mock_cls = MagicMock()
    mock_cls.return_value.collect_discussions.return_value = [
        {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "ext1"},
    ]

    mock_hatchet = MagicMock()

    engine = MagicMock()
    mock_disc_row = MagicMock()
    mock_disc_row.id = 42
    mock_disc_row.content_id = 100
    mock_disc_row.domain = "reddit.com"
    mock_disc_row.text = "This is a Reddit self-post"
    mock_disc_row.discussions_searched_at = None  # Not yet searched
    mock_result = MagicMock()
    mock_result.first.return_value = mock_disc_row
    connect_mock = MagicMock()
    connect_mock.execute.return_value = mock_result
    engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
    engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)

    mock_hatchet.event.push.assert_called_once()
```

- [ ] **Step 4: Run test to verify it passes (self-post test should already pass with current code)**

Run: `uv run pytest tests/workflows/test_collection.py::TestEventEmission::test_emits_event_for_self_post -v`
Expected: PASS — current code always emits, so this passes before the change too.

- [ ] **Step 5: Add `events_skipped` field to `CollectResult`**

In `src/aggre/workflows/models.py`, add to `CollectResult`:

```python
class CollectResult(TaskResult):
    """Collection result with source identifier."""

    source: str = ""
    event_errors: int = 0  # Items processed but failed to emit downstream event
    events_skipped: int = 0  # Items skipped because already fully processed (dedup)
```

- [ ] **Step 6: Implement the emission-time check**

In `src/aggre/workflows/collection.py`, change `_emit_item_event` return type from `bool` to
`str` ("emitted", "skipped", or "error"), update `collect_source` to track skipped count, and
include it in log output.

Change `_emit_item_event`:

```python
def _emit_item_event(
    engine: sa.engine.Engine,
    hatchet: Hatchet,
    ref: dict,
    source_name: str,
) -> str:
    """Emit an 'item.new' event for a processed discussion.

    Returns "emitted", "skipped" (fully processed, dedup), or "error".
    """
    try:
        with engine.connect() as conn:
            disc = conn.execute(
                sa.select(
                    SilverDiscussion.id,
                    SilverDiscussion.content_id,
                    SilverContent.domain,
                    SilverContent.text,
                    SilverContent.discussions_searched_at,
                )
                .outerjoin(SilverContent, SilverContent.id == SilverDiscussion.content_id)
                .where(
                    SilverDiscussion.source_type == source_name,
                    SilverDiscussion.external_id == ref["external_id"],
                )
            ).first()

        if disc and disc.content_id:
            # -- Event dedup (Layer 1) --
            # Skip emitting if the content is fully processed: text extracted/transcribed
            # AND discussion search completed. This prevents collectors from flooding the
            # Hatchet queue with redundant events for items seen on every cron cycle.
            #
            # We check BOTH columns because self-posts (Reddit selftext, Ask HN,
            # Telegram messages) pre-populate SilverContent.text at collection time.
            # Checking text alone would suppress events for self-posts on their very
            # first collection, preventing discussion-search and comments from running.
            # The discussions_searched_at column is only set after the discussion-search
            # workflow completes, so it reliably indicates "all downstream work is done."
            #
            # Layer 2 (CANCEL_NEWEST per content_id on each workflow) provides a safety
            # net for race conditions where an event slips through during the brief
            # window between collection and workflow completion.
            if disc.text is not None and disc.discussions_searched_at is not None:
                logger.info(
                    "collect.event_skipped_fully_processed source=%s external_id=%s content_id=%s",
                    source_name, ref["external_id"], disc.content_id,
                )
                return "skipped"

            event = ItemEvent(
                content_id=disc.content_id,
                discussion_id=disc.id,
                source=source_name,
                domain=disc.domain,
            )
            hatchet.event.push("item.new", event.model_dump(), options=PushEventOptions(scope="default"))
        return "emitted"
    except Exception:
        logger.exception("collect.event_emit_error source=%s external_id=%s", source_name, ref["external_id"])
        return "error"
```

Change `collect_source` to track skipped count:

```python
    count = 0
    errors = 0
    event_errors = 0
    events_skipped = 0
    for ref in refs:
        try:
            with engine.begin() as conn:
                collector.process_discussion(ref["raw_data"], conn, ref["source_id"])
            count += 1

            # Emit event for downstream processing workflows
            if hatchet is not None:
                result = _emit_item_event(engine, hatchet, ref, name)
                if result == "error":
                    event_errors += 1
                elif result == "skipped":
                    events_skipped += 1
        except Exception:
            logger.exception("collect.process_error source=%s external_id=%s", name, ref["external_id"])
            errors += 1
    logger.info(
        "collect.source_complete source=%s fetched=%d processed=%d errors=%d event_errors=%d events_skipped=%d",
        name, len(refs), count, errors, event_errors, events_skipped,
    )
    return CollectResult(
        source=name, succeeded=count, failed=errors, total=len(refs),
        event_errors=event_errors, events_skipped=events_skipped,
    )
```

Also update the `ctx.log` in `register` to include skipped count:

```python
ctx.log(f"Collected {result.succeeded} discussions from {_name} (errors={result.failed}, event_errors={result.event_errors}, events_skipped={result.events_skipped})")
```

- [ ] **Step 7: Fix existing tests**

The existing tests need two fixes: (a) add explicit `None` for new columns on mock rows
(MagicMock auto-creates truthy attributes which would trigger the dedup check), and
(b) update assertions that check `_emit_item_event` return value (now `str`, not `bool`).

In `test_emits_item_new_event`, add after `mock_disc_row.domain = "example.com"` (line 117):
```python
mock_disc_row.text = None
mock_disc_row.discussions_searched_at = None
```

In `test_event_emission_error_doesnt_crash`, add after `mock_disc_row.domain = "example.com"` (line 167):
```python
mock_disc_row.text = None
mock_disc_row.discussions_searched_at = None
```

And update the assertion on line 177 from:
```python
assert result == CollectResult(source="hackernews", succeeded=2, failed=0, total=2, event_errors=2)
```
to:
```python
assert result == CollectResult(source="hackernews", succeeded=2, failed=0, total=2, event_errors=2, events_skipped=0)
```

Also update `test_no_event_when_fully_processed` assertion to check `events_skipped=1`:
```python
result = collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)
assert result.events_skipped == 1
mock_hatchet.event.push.assert_not_called()
```

- [ ] **Step 8: Run all emission tests**

Run: `uv run pytest tests/workflows/test_collection.py::TestEventEmission -v`
Expected: ALL PASS. The new fully-processed test passes (event suppressed, `events_skipped=1`).
The self-post test passes (event emitted). Existing tests pass with explicit `None` values.

- [ ] **Step 9: Run full test file**

Run: `uv run pytest tests/workflows/test_collection.py -v`
Expected: ALL PASS

- [ ] **Step 10: Commit**

```bash
git add src/aggre/workflows/collection.py src/aggre/workflows/models.py tests/workflows/test_collection.py
git commit -m "feat: skip event emission for fully-processed content

Layer 1 of event dedup: check text IS NOT NULL AND
discussions_searched_at IS NOT NULL before emitting item.new.
Prevents 99% of redundant Hatchet queue spam from cron collectors
re-emitting events for already-processed items.

Self-posts (text pre-populated by collector) still emit because
discussions_searched_at remains NULL until search completes."
```

---

## Chunk 2: CANCEL_NEWEST concurrency on all event-driven workflows

### Task 2: Add CANCEL_NEWEST per content_id to process-webpage

**Files:**
- Modify: `src/aggre/workflows/webpage.py:281-293`
- Test: `tests/workflows/test_collection.py` (or inline verification — registration is `pragma: no cover`)

- [ ] **Step 1: Modify webpage workflow registration**

In `src/aggre/workflows/webpage.py`, change the `register` function's `concurrency` from a single expression to a list:

```python
def register(h):  # pragma: no cover — Hatchet wiring
    """Register the webpage workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-webpage",
        on_events=["item.new"],
        # Two-layer concurrency:
        # 1. GROUP_ROUND_ROBIN by domain — fair scheduling across domains, max 3 per domain
        # 2. CANCEL_NEWEST by content_id — if a run for the same content_id is already
        #    in-flight (queued or running), the new run is immediately cancelled.
        #    This is Layer 2 of event dedup: a safety net for race conditions where
        #    _emit_item_event's check passes but the content gets processed before
        #    the queued task starts. See docs/superpowers/specs/2026-03-16-event-dedup-design.md
        concurrency=[
            ConcurrencyExpression(
                expression="input.domain",
                max_runs=3,
                limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
            ),
            ConcurrencyExpression(
                expression="string(input.content_id)",
                max_runs=1,
                limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
            ),
        ],
        input_validator=ItemEvent,
        default_filters=[DefaultFilter(expression=_webpage_filter_expr, scope="default")],
    )
```

The rest of the function (task definitions) stays unchanged.

- [ ] **Step 2: Verify lint passes**

Run: `uv run ruff check src/aggre/workflows/webpage.py`
Expected: No errors

### Task 3: Add CANCEL_NEWEST per content_id to process-comments

**Files:**
- Modify: `src/aggre/workflows/comments.py:62-74`

- [ ] **Step 1: Modify comments workflow registration**

In `src/aggre/workflows/comments.py`, change the `register` function:

```python
def register(h):  # pragma: no cover — Hatchet wiring
    """Register the comments workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-comments",
        on_events=["item.new"],
        # Two-layer concurrency:
        # 1. GROUP_ROUND_ROBIN by source — fair scheduling across sources, max 5 per source
        # 2. CANCEL_NEWEST by content_id — dedup safety net, see event-dedup-design.md
        concurrency=[
            ConcurrencyExpression(
                expression="input.source",
                max_runs=5,
                limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
            ),
            ConcurrencyExpression(
                expression="string(input.content_id)",
                max_runs=1,
                limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
            ),
        ],
        input_validator=ItemEvent,
        default_filters=[DefaultFilter(expression=_comments_filter_expr, scope="default")],
    )
```

- [ ] **Step 2: Verify lint passes**

Run: `uv run ruff check src/aggre/workflows/comments.py`
Expected: No errors

### Task 4: Add CANCEL_NEWEST per content_id to process-discussion-search

**Files:**
- Modify: `src/aggre/workflows/discussion_search.py:101-113`

- [ ] **Step 1: Modify discussion-search workflow registration**

In `src/aggre/workflows/discussion_search.py`, change the `register` function:

```python
def register(h):  # pragma: no cover — Hatchet wiring
    """Register the discussion search workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-discussion-search",
        on_events=["item.new"],
        # Two-layer concurrency:
        # 1. GROUP_ROUND_ROBIN with static key — global max 5 concurrent searches
        # 2. CANCEL_NEWEST by content_id — dedup safety net, see event-dedup-design.md
        concurrency=[
            ConcurrencyExpression(
                expression="'search'",
                max_runs=5,
                limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
            ),
            ConcurrencyExpression(
                expression="string(input.content_id)",
                max_runs=1,
                limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
            ),
        ],
        input_validator=ItemEvent,
        default_filters=[DefaultFilter(expression=_search_filter_expr, scope="default")],
    )
```

- [ ] **Step 2: Verify lint passes**

Run: `uv run ruff check src/aggre/workflows/discussion_search.py`
Expected: No errors

### Task 5: Add CANCEL_NEWEST per content_id to process-transcription

**Files:**
- Modify: `src/aggre/workflows/transcription.py:207-219`

- [ ] **Step 1: Modify transcription workflow registration**

In `src/aggre/workflows/transcription.py`, change the `register` function:

```python
def register(h):  # pragma: no cover — Hatchet wiring
    """Register the transcription workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-transcription",
        on_events=["item.new"],
        # Two-layer concurrency:
        # 1. GROUP_ROUND_ROBIN with static key — global max 20 concurrent transcriptions
        # 2. CANCEL_NEWEST by content_id — dedup safety net, see event-dedup-design.md
        concurrency=[
            ConcurrencyExpression(
                expression="'youtube'",
                max_runs=20,
                limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
            ),
            ConcurrencyExpression(
                expression="string(input.content_id)",
                max_runs=1,
                limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
            ),
        ],
        input_validator=ItemEvent,
        default_filters=[DefaultFilter(expression="input.domain == 'youtube.com'", scope="default")],
    )
```

- [ ] **Step 2: Verify lint passes**

Run: `uv run ruff check src/aggre/workflows/transcription.py`
Expected: No errors

### Task 6: Final verification and commit

- [ ] **Step 1: Run full test suite**

Run: `make test-e2e`
Expected: ALL PASS

- [ ] **Step 2: Run lint**

Run: `make lint`
Expected: ALL PASS

- [ ] **Step 3: Run coverage-diff**

Run: `make coverage-diff`
Expected: PASS (≥95% diff coverage). The workflow registrations are `pragma: no cover`, so they don't affect coverage. The `_emit_item_event` changes are covered by the new tests.

- [ ] **Step 4: Commit**

```bash
git add src/aggre/workflows/webpage.py src/aggre/workflows/comments.py src/aggre/workflows/discussion_search.py src/aggre/workflows/transcription.py
git commit -m "feat: add CANCEL_NEWEST per content_id on all event-driven workflows

Layer 2 of event dedup: if a workflow run for the same content_id
is already in-flight, new runs are immediately cancelled by Hatchet.
Safety net for race conditions not caught by the emission-time check.

Applied to: process-webpage, process-comments, process-discussion-search,
process-transcription. Each workflow now has two concurrency expressions:
the original GROUP_ROUND_ROBIN plus CANCEL_NEWEST on content_id."
```

---

## Chunk 3: Post-deployment queue cleanup

### Task 7: Cancel queued process-webpage tasks

After deploying, cancel only **process-webpage** queued tasks (~20K). These are the slot hog —
99% are no-ops that discover "already_done" after consuming 2 slots (download + extract DAG).

**Why only webpage?** The other workflows have legitimate backlog:
- `process-discussion-search` (~20K queued): 27,880 items genuinely need searching
- `process-comments` (~12K queued): 12,837 items genuinely need comment fetching
- `process-transcription` (~16.5K queued): YouTube items needing transcription

Cancelling those would lose work that won't be re-triggered (collectors only emit for items
they see on the current cron cycle, not the full historical backlog).

**What about the ~1% of real webpage tasks?** At most ~5.6K items still need text extraction.
These will be re-triggered on the next hourly collector run — the emission check won't suppress
them because `text IS NULL`.

**Note:** The worker API token returns 403 on cancel endpoints. Use the Hatchet UI admin session.

- [ ] **Step 1: Cancel queued process-webpage tasks via Hatchet UI**

Open `http://hatchet.ts.shen.iorlas.net` in a browser (requires Tailscale).

1. Navigate to `process-webpage` workflow runs
2. Filter by status: QUEUED
3. Select all and cancel

If the UI doesn't support bulk cancel, log in to get a session cookie, then use the API:

```bash
# Get the cookie from browser devtools after logging in
COOKIE="hatchet=<session_cookie_value>"
TENANT="707d0855-80ab-4e1f-a156-f1c4546cbf52"
BASE="http://hatchet.ts.shen.iorlas.net/api/v1/stable"
WEBPAGE_WF="f34fb364-b71e-4733-924f-4ca407517b40"

# Paginate through queued webpage runs and cancel in batches
while true; do
  IDS=$(curl -s "$BASE/tenants/$TENANT/workflow-runs?statuses=QUEUED&workflow_ids=$WEBPAGE_WF&limit=100&only_tasks=false&since=2026-03-15T00:00:00Z" \
    -H "Cookie: $COOKIE" | jq -r '[.rows[].taskExternalId] | join(",")')
  [ -z "$IDS" ] || [ "$IDS" = "null" ] && break
  for id in $(echo "$IDS" | tr ',' '\n'); do
    curl -s -X POST "$BASE/workflow-runs/$id/cancel" -H "Cookie: $COOKIE" -H "Content-Type: application/json"
  done
  echo "Cancelled batch of 100..."
done
```

- [ ] **Step 2: Verify webpage queue is clear, others untouched**

```bash
TOKEN="<worker_token>"
BASE="http://hatchet.ts.shen.iorlas.net/api/v1/stable/tenants/707d0855-80ab-4e1f-a156-f1c4546cbf52/workflow-runs"

for wf_name in process-webpage process-discussion-search process-comments process-transcription; do
  wf_id=$(curl -s "http://hatchet.ts.shen.iorlas.net/api/v1/tenants/707d0855-80ab-4e1f-a156-f1c4546cbf52/workflows" \
    -H "Authorization: Bearer $TOKEN" | jq -r ".rows[] | select(.name == \"$wf_name\") | .metadata.id")
  pages=$(curl -s "$BASE?since=2026-03-15T00:00:00Z&only_tasks=false&statuses=QUEUED&workflow_ids=$wf_id&limit=100" \
    -H "Authorization: Bearer $TOKEN" | jq '.pagination.num_pages')
  echo "$wf_name: ~$((pages * 100)) queued"
done
```

Expected: `process-webpage` near zero. Others unchanged.

- [ ] **Step 3: Monitor throughput recovery**

Check discussion-search throughput over the next hour:

```sql
SELECT
  date_trunc('hour', discussions_searched_at::timestamptz) as hour,
  count(*) as items_searched
FROM silver_content
WHERE discussions_searched_at IS NOT NULL
  AND discussions_searched_at::timestamptz >= now() - interval '3 hours'
GROUP BY 1 ORDER BY 1;
```

Expected: Throughput recovers to 200+ items/hr as slots freed from no-op webpage tasks go to real work.
