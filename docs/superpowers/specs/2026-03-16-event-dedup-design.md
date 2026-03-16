# Event Deduplication: Emission Check + CANCEL_NEWEST Safety Net

## Problem

Collectors emit `item.new` events for every discussion they process, including items already fully processed by downstream workflows. With hourly crons across 9 sources, this creates thousands of redundant events per hour. Each event triggers 4 workflows (webpage, transcription, comments, discussion-search), and 99% of resulting tasks discover "already_done" and skip — but only after consuming a Hatchet scheduler slot and waiting in a 52K+ task queue.

The 2-step webpage DAG makes this worse: each no-op burns two slots sequentially (download_task → extract_task), with 48-396 seconds of Hatchet scheduling overhead between steps.

## Solution

Two-layer dedup: cheap emission-time check (kills 99% of spam) + Hatchet CANCEL_NEWEST per content_id (safety net for races).

### Layer 1: Emission-time check in `_emit_item_event`

In `collection.py:_emit_item_event`, add `SilverContent.text` and `SilverContent.discussions_searched_at` to the existing query. If both are non-null, skip emitting the event.

**Why two columns?** Self-posts (Reddit selftext, Ask HN, Telegram messages) pre-populate `SilverContent.text` at collection time. Checking `text IS NOT NULL` alone would suppress events for these items on their very first collection, preventing comments and discussion-search from ever firing. Adding `discussions_searched_at IS NOT NULL` ensures we only suppress fully-processed items where all downstream work is complete.

**What about comments?** Comments have their own `comments_fetched_at IS NOT NULL` / `comments_json IS NOT NULL` guard at the task level. The CANCEL_NEWEST layer handles any remaining duplicates. Adding a third column check to the emitter would over-couple it.

**Note on existing guards:** All four event-driven workflows already have "already_done" guards at the task level (webpage: `text IS NOT NULL`, transcription: `text IS NOT NULL`, comments: `comments_json IS NOT NULL`). These two new layers are performance optimizations to avoid queue/scheduling overhead, not correctness fixes.

**Change scope:** ~5 lines in `_emit_item_event`. Add `SilverContent.text, SilverContent.discussions_searched_at` to the existing `sa.select()`, check both before emitting.

### Layer 2: CANCEL_NEWEST per content_id on all event-driven workflows

Add a second `ConcurrencyExpression` to all four event-driven workflows (webpage, transcription, comments, discussion-search):

```python
concurrency=[
    # Existing expression (domain/source/static group)
    ConcurrencyExpression(
        expression="input.domain",  # varies per workflow
        max_runs=3,
        limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
    ),
    # Safety net: one run per content_id at a time, duplicates cancelled immediately
    ConcurrencyExpression(
        expression="string(input.content_id)",
        max_runs=1,
        limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
    ),
]
```

**Behavior:** If a run for `content_id=42` is already in progress (queued or running), any new run for the same content_id is immediately cancelled — no slot consumed, no queue time.

**Why both layers?** The emission check prevents most spam at the source. CANCEL_NEWEST handles race conditions (collector emits event, then workflow completes before the event is processed — next collector run would emit again for the brief window where text was NULL).

## Files Changed

| File | Change |
|------|--------|
| `src/aggre/workflows/collection.py` | Add `SilverContent.text` and `.discussions_searched_at` to query in `_emit_item_event`, skip if both non-null |
| `src/aggre/workflows/webpage.py` | Change `concurrency` from single expression to list, add CANCEL_NEWEST on `string(input.content_id)` |
| `src/aggre/workflows/transcription.py` | Same: add CANCEL_NEWEST on `string(input.content_id)` |
| `src/aggre/workflows/comments.py` | Same: add CANCEL_NEWEST on `string(input.content_id)` |
| `src/aggre/workflows/discussion_search.py` | Same: add CANCEL_NEWEST on `string(input.content_id)` |

## Testing

- **Unit test for `_emit_item_event`**: mock engine returns row with `text=None, discussions_searched_at=None` → event emitted; row with `text="some text", discussions_searched_at="2026-..."` → event skipped; row with `text="selfpost", discussions_searched_at=None` → event emitted (self-post case).
- **Unit tests for workflow registrations**: verify each workflow's concurrency is a list with two expressions, second being CANCEL_NEWEST on content_id.
- **Integration**: deploy, verify queue stops growing, monitor `discussions_searched_at` throughput recovery.

## Expected Impact

- Queue shrinks from 52K to near-zero (only genuinely new items queued)
- Discussion search throughput recovers to 200+/hr (slots freed from no-op webpage tasks)
- Hatchet scheduler latency drops (fewer tasks to evaluate per tick)
- No architectural changes, no new dependencies, no infrastructure changes

### Layer 3: `text_provided` structural filter on ItemEvent

Added `text_provided: bool` to `ItemEvent`. Set at emission time: `True` when `SilverContent.text`
is already populated (self-posts, Ask HN text, Telegram messages). Webpage and transcription
workflow filters check `!input.text_provided` — tasks are never queued, zero slot overhead.

**Why this is NOT processing state:** `text_provided` is a structural property of the content type.
A Reddit self-post will never need webpage download regardless of when you check. This is different
from `has_comments` or `has_search` which are processing state that changes over time — those
flags would suppress re-processing and should NOT be used in event filters.

## Architectural Notes for Future Refactoring

The current `item.new` event is generic — every consumer decides independently whether to process.
This works but has fundamental tensions:

1. **Boolean signal flags on a generic event are a poor man's explicit events.** Adding `text_provided`,
   `has_comments`, `has_search` to `item.new` is logically equivalent to emitting separate events
   (`text.needed`, `comments.needed`, `search.needed`). The generic event approach encodes the same
   routing information, just less cleanly.

2. **Snapshot-based flags can suppress re-processing.** Processing-state flags (`has_comments=true`)
   encode a snapshot at emission time. If new comments appear later, the flag would prevent re-checking.
   Only structural flags (like `text_provided`) are safe in event filters — processing state belongs
   inside the workflow task where the DB is the source of truth.

3. **The real problem is slot consumption before filtering.** Hatchet's `default_filters` solve this
   for event-level properties. But any check that requires DB state (is this content already processed?)
   currently happens inside the task after a slot is consumed. A proper solution might be:
   - Explicit event types per action needed (collector decides what processing is required)
   - Or a pre-queue hook that checks DB state before slot allocation
   - Or migrating to a platform with native workflow-ID dedup (Temporal)

4. **Collector-as-orchestrator vs pub/sub.** Today collectors emit generic events and workflows
   self-filter. The alternative: collectors emit specific events for each needed action. This is
   tighter coupling but more efficient — no wasted filter evaluations. The right answer depends on
   how often new workflow types are added vs how often collector behavior changes.

These are noted for future reference. The current layered approach (emission check + structural
filter + CANCEL_NEWEST + task-level guard) is pragmatic and effective for the current scale.

## Out of Scope

- Cancelling existing 52K queued tasks (manual via Hatchet API or let them drain)
- Adding `discussions_searched_at` guard to discussion-search workflow (CANCEL_NEWEST covers this)
- Hatchet-lite performance tuning (buffer env vars) — separate concern
- Migration to Temporal or other workflow engine
- Explicit event types per action (future refactoring — see architectural notes above)
