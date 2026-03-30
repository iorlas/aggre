# Per-Source Comment Fetch with Change Detection

**Date:** 2026-03-30
**Status:** Draft

## Summary

Replace the single `process-comments` workflow (triggered by `item.new` + CEL filter) with three per-source workflows, each triggered by a dedicated event (`comments.fetch.hackernews`, `comments.fetch.reddit`, `comments.fetch.lobsters`). Add change detection during hourly collection: when `comment_count` increases and the last fetch was >30 minutes ago, re-emit the event. The same workflow handles both initial fetch and refresh — no distinction.

## Motivation

Comments are fetched once, immediately after a post is discovered. For posts discovered shortly after submission, this captures only a handful of early comments. Most community discussion arrives 6–24 hours later and is never collected. Community opinions are critical to the aggregation — a post with 3 comments looks the same as one with 300 in our system.

The hourly collection already re-fetches each source's index and upserts `score` and `comment_count`. This data is available for free — we just need to compare and react.

## Design

### Events

Three new event types replace the comment branch of `item.new`:

- `comments.fetch.hackernews`
- `comments.fetch.reddit`
- `comments.fetch.lobsters`

Each carries a `CommentFetchRef` payload:

```python
class CommentFetchRef(BaseModel):
    discussion_id: int
    source: str        # "hackernews", "reddit", "lobsters"
    external_id: str   # needed by collector to build API URL
    meta_json: str | None = None  # needed by Reddit (contains subreddit)
```

### Emission Points

Events are emitted from `collection.py` (which has Hatchet access) in two scenarios:

**1. New discussion (insert).** After `_upsert_discussion` returns a new `id` (not `None`), emit `comments.fetch.{source}` if source is in the comment-capable set. This replaces the current `item.new` path for comments.

**2. Changed discussion (update).** Before the upsert, read the current `comment_count` and `comments_fetched_at` from the DB. After the upsert completes, compare:
- Did the incoming `comment_count` increase compared to the previously stored value?
- Is `comments_fetched_at` either `NULL` or older than 30 minutes?

If both conditions are true, emit `comments.fetch.{source}`.

**Important ordering:** The change detection SELECT must happen BEFORE `_upsert_discussion`, because the upsert overwrites `comment_count` with the new value. Reading after would always find them equal. The helper `should_refetch_comments` takes the incoming count and compares against the pre-upsert DB state.

All emission logic lives in `collection.py`, not in the collectors. Collectors don't have Hatchet access. The collection loop calls `should_refetch_comments` before the upsert, then emits if needed after the upsert succeeds.

### Per-Source Workflows

Each source gets its own workflow in `workflows/comments.py`. All three follow the same pattern but are registered independently with their own concurrency config.

```python
# Example for hackernews — reddit and lobsters follow the same shape
wf = h.workflow(
    name="comments-fetch-hackernews",
    on_events=["comments.fetch.hackernews"],
    concurrency=[
        ConcurrencyExpression(
            expression="string(input.discussion_id)",
            max_runs=1,
            limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
        ),
    ],
    input_validator=CommentFetchRef,
)
```

No CEL `default_filters` — the event type itself is the routing. No `comments_json IS NULL` guard — the workflow fetches unconditionally and overwrites.

Concurrency: one run per `discussion_id` (dedup), no per-source global limit initially. The 30-minute cooldown at emission time is the primary rate control. If needed, a per-source `max_runs` can be added later.

### Collector Changes

No changes to the `fetch_discussion_comments()` method signatures or implementations. They already fetch and overwrite via `_mark_comments_done()`. The only behavioral change is that they get called more than once per discussion.

### What `_mark_comments_done` Already Does

```python
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

This is already idempotent — overwrites are safe. `comments_fetched_at` updates on every call, which is what the cooldown check needs.

### Change Detection Helper

A new standalone function in `collectors/base.py` (called from `collection.py` before the upsert):

```python
def should_refetch_comments(
    conn: sa.Connection,
    discussion_id: int,
    new_comment_count: int,
    cooldown_minutes: int = 30,
) -> bool:
    """Check if comments should be (re)fetched based on count change and cooldown."""
    row = conn.execute(
        sa.select(
            SilverDiscussion.comment_count,
            SilverDiscussion.comments_fetched_at,
        ).where(SilverDiscussion.id == discussion_id)
    ).first()

    if row is None:
        return False

    stored_count = row.comment_count or 0
    if new_comment_count <= stored_count:
        return False  # no new comments

    if row.comments_fetched_at is None:
        return True  # never fetched

    fetched_at = datetime.fromisoformat(row.comments_fetched_at)
    return (datetime.now(UTC) - fetched_at).total_seconds() > cooldown_minutes * 60
```

### Removal of `item.new` Comment Path

The CEL filter `input.source in ['hackernews', 'lobsters', 'reddit']` on the `process-comments` workflow is removed. The entire `process-comments` workflow is deleted. Comments no longer flow through `item.new` at all.

The `_emit_item_event` function in `collection.py` continues to emit `item.new` for webpage/transcription workflows — no change there.

### Column Ownership

No change. Comments workflow still writes only to `silver_discussions`: `comments_json`, `comment_count`, `comments_fetched_at`. These columns are not written by any other workflow.

### Recovery

If a bad deploy writes corrupt `comments_json`:
1. Identify the blast radius via `comments_fetched_at` (query the affected time window)
2. NULL out `comments_json` and `comments_fetched_at` for those rows
3. Next hourly collection detects `comment_count > 0` with `comments_fetched_at IS NULL` → re-emits events → re-fetches

No manual event pushing needed — the system self-heals on the next collection cycle.

## Testing

- **Unit tests for `should_refetch_comments`**: new discussion (never fetched), count increased within cooldown, count increased outside cooldown, count unchanged, count decreased (API fluctuation — should not refetch).
- **Unit tests for emission logic**: new discussion emits event, existing discussion with changed count emits event, existing discussion within cooldown does not emit, existing discussion with unchanged count does not emit.
- **Integration test**: verify that a discussion processed twice with increasing `comment_count` triggers two comment fetches with >30min gap simulation.
- **Existing comment fetch tests**: update to use new event names, remove `comments_json IS NULL` guard assertions.

## Files Changed

| File | Change |
|------|--------|
| `src/aggre/workflows/comments.py` | Delete `process-comments` workflow. Add three per-source workflows (`comments-fetch-hackernews`, `comments-fetch-reddit`, `comments-fetch-lobsters`). Drop null guard. |
| `src/aggre/workflows/models.py` | Add `CommentFetchRef` model. |
| `src/aggre/workflows/collection.py` | Emit `comments.fetch.{source}` on new discussion insert AND on change detection. Remove comment-related logic from `_emit_item_event`. |
| `src/aggre/collectors/base.py` | Add `should_refetch_comments()` helper. |
| `src/aggre/collectors/hackernews/collector.py` | No changes needed (emission handled in `collection.py`). |
| `src/aggre/collectors/reddit/collector.py` | No changes needed. |
| `src/aggre/collectors/lobsters/collector.py` | No changes needed. |
| `tests/` | Update existing comment workflow tests, add tests for change detection and emission logic. |

## Risks

**Thundering herd on first deploy.** Every existing discussion with `comments_fetched_at IS NULL` (never fetched) OR stale comments would trigger re-fetch on the first collection run. Mitigation: the `CANCEL_NEWEST` concurrency on `discussion_id` deduplicates, and the system already handles 20 concurrent comment fetches per source. The initial burst works through the queue over a few hours.

**API rate limits from increased fetches.** The 30-minute cooldown limits each discussion to ~48 re-fetches/day maximum (practically much less — `comment_count` stabilizes). Per-source rate limiting in collectors still applies.

**Stale `comment_count` comparison.** The SELECT for current count and the upsert are not atomic. A concurrent comment fetch could update `comments_fetched_at` between the read and the emit. Worst case: one unnecessary re-fetch, caught by `CANCEL_NEWEST`. Acceptable.

## What Is NOT Changed

- Webpage and transcription workflows — still triggered by `item.new`, unchanged.
- `_emit_item_event` — still emits `item.new` for content processing. Comment emission moves to a separate code path.
- Collector `fetch_discussion_comments` implementations — method signatures and logic unchanged.
- Bronze layer — raw comment JSON still archived on every fetch.
- Database schema — no new columns, no migrations needed.
