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

### Why we need four guardrails (root cause)

The core architecture problem is NOT Hatchet's speed — it's that the `item.new` event is too
thin for consumers to self-filter without side effects. Every workflow fires for every item,
queries the DB to discover "not for me", and skips — but only after consuming a scheduler slot.

Hatchet's slowness made the waste visible (52K queue, 48s DAG transitions). But even with a
fast runtime (Temporal, Celery), the architecture still wastes work: four workflows fire, two
discover they have nothing to do, two do real work. The guardrails we built mitigate this:

1. **Emission check** — compensates for collectors re-seeing old items every cron cycle
2. **CANCEL_NEWEST** — compensates for duplicate events arriving for in-flight work
3. **`text_provided` filter** — compensates for thin events that can't distinguish content types
4. **Task-level guards** — compensates for all of the above failing

Every guardrail exists because the event discards information the collector already has.

### The `text_provided` semantic gap

`text_provided` is set as `disc.text is not None` at emission time. The comment says "structural
signal — collector provided the text" but the implementation captures processing state too (text
previously extracted by the webpage workflow reads as `text_provided=True`). This doesn't cause
bugs today because the emission-time dedup check (Layer 1) catches fully-processed items before
`text_provided` is evaluated. But it's a semantic lie that would break if re-fetching were added.

The proper fix: `process_discussion` should return what it created (including whether it set text),
so the emission code uses the collector's own output rather than re-querying DB state. This is
prescribed as part of the richer event refactoring below.

### Prescribed refactoring: richer event contract

**The pattern is correct — pub/sub with self-filtering consumers is idiomatic.** The problem is
that the event doesn't describe the content well enough for consumers to self-select without
DB queries. The fix is NOT push-driven orchestration (collector says "run webpage, run comments")
which would couple collectors to downstream workflows.

The fix is a **richer event** where the collector describes what it found, and each workflow
declares what kinds of content it processes:

```python
class ItemEvent(BaseModel):
    content_id: int
    discussion_id: int
    source: str
    domain: str | None = None
    # Content description — set by collector from its own output
    has_external_url: bool = True    # False for self-posts, Ask HN, Telegram
    content_type: str = "link"       # "link", "self_post", "video", "paper", ...
```

Each workflow's filter becomes a pure declaration over content properties:

```python
# Webpage: only items with an external URL to fetch
"input.has_external_url && !(input.domain in [...])"

# Transcription: only video content
"input.content_type == 'video'"

# Comments: source-based (unchanged)
"input.source in ['reddit', 'hackernews', 'lobsters']"

# Discussion search: domain-based (unchanged)
"!(input.domain in [...])"
```

This preserves pub/sub decoupling: collectors don't know about workflows, workflows don't know
about collectors. The event contract describes the content, not the required processing.

**Implementation approach:**
1. Have `process_discussion` return a result object with the content properties it created
   (content_id, discussion_id, domain, text, content_type, has_external_url, etc.)
2. The emission code constructs ItemEvent from the collector's return value — no DB re-query
3. Enrich ItemEvent with content-description fields
4. Update workflow filters to use the new fields
5. Remove `text_provided` (subsumed by `has_external_url` / `content_type`)
6. Emission-time dedup check may still be useful to avoid the gRPC call to Hatchet for
   fully-processed items seen on every cron cycle — evaluate whether the filter rejection
   (zero overhead) makes this unnecessary

This eliminates the semantic gap, removes most guardrails, and keeps the architecture idiomatic.

### What NOT to do

- **Don't add processing-state flags** (`has_comments`, `has_search`) to the event. These are
  snapshots that suppress re-processing. Processing state belongs inside the workflow task
  where the DB is the source of truth.
- **Don't make collectors orchestrate** ("run webpage, run comments"). This couples collectors
  to downstream workflows and breaks pub/sub.
- **Don't add more guardrails.** The current four are already a sign of architectural tension.
  The next step should reduce guardrails, not add more.

## Out of Scope

- Cancelling existing 52K queued tasks (manual via Hatchet API or let them drain)
- Adding `discussions_searched_at` guard to discussion-search workflow (CANCEL_NEWEST covers this)
- Hatchet-lite performance tuning (buffer env vars) — separate concern
- Migration to Temporal or other workflow engine
- Explicit event types per action (future refactoring — see architectural notes above)
