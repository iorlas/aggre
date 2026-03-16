# Richer Event Contract: `process_discussion` returns `DiscussionResult`

## Problem

The `item.new` event is built by re-querying the DB after `process_discussion` runs. This causes
a semantic gap: the emission code infers content properties from DB state (e.g., `text_provided =
disc.text is not None`) but can't distinguish "collector provided text" from "previous webpage run
extracted text." The collector already knows what it created — it just discards that knowledge.

## Solution

Have `process_discussion` return a `DiscussionResult` with the content properties it created.
The emission code builds `ItemEvent` directly from the result — no DB re-query for content-type
properties, no inference.

### `DiscussionResult` TypedDict

Defined in `collectors/base.py` alongside the existing `DiscussionRef`:

```python
class DiscussionResult(TypedDict):
    content_id: int | None
    discussion_id: int       # always set — _upsert_discussion now always returns id
    domain: str | None
    has_external_url: bool   # True for link posts, False for self-posts/text content
```

Return type of `process_discussion` becomes `DiscussionResult | None`. Returns `None` on early
exits (invalid data, missing external_id, etc.) — `collect_source` skips event emission for `None`.

This is NOT a DB row — it's a content description returned by the collector. It's stable across
schema changes because it describes what the collector found, not how it's stored.

### `ItemEvent` changes

Replace `text_provided: bool` with `has_external_url: bool`:

```python
class ItemEvent(BaseModel):
    content_id: int
    discussion_id: int
    source: str
    domain: str | None = None
    has_external_url: bool = True  # False for self-posts, Ask HN text, Telegram messages
```

`has_external_url` describes the content type authoritatively (from the collector), not processing
state. Default is `True` (most items are link posts).

**In-flight event compatibility:** Events already in the Hatchet queue have `text_provided` in
their payload. The new filter expressions reference `input.has_external_url`. To avoid breakage
during deployment, briefly keep `text_provided` as a deprecated field on `ItemEvent` with default
`False`. Remove it after one full cron cycle (~3 hours). Alternatively, drain the queue before
deploying (the real queue depth is ~5 items as of the cleanup).

### Workflow filter changes

```python
# Webpage: only items with an external URL (and not in skip domains)
_webpage_filter_expr = f"input.has_external_url && !({_skip_domain_expr})"

# Transcription: only YouTube
# Note: has_external_url is always True for YouTube (collector always calls ensure_content),
# so the clause is technically redundant but kept for clarity and safety.
"input.domain == 'youtube.com' && input.has_external_url"

# Comments: unchanged (filters on source, not content type)
# Discussion search: unchanged (filters on domain)
```

### `_emit_item_event` changes

Takes `DiscussionResult` from the collector. Content-type properties (`has_external_url`, `domain`,
`content_id`) come from the result. Processing state (`text`, `discussions_searched_at`) still comes
from a single DB query — this is correct because processing state belongs in the DB.

```python
def _emit_item_event(
    engine: sa.engine.Engine,
    hatchet: Hatchet,
    ref: dict,
    source_name: str,
    result: DiscussionResult,
) -> str:
    try:
        if not result["content_id"]:
            return "emitted"  # No content linked, nothing to process

        # Emission dedup: check processing state (still needs DB — collector doesn't know this)
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(SilverContent.text, SilverContent.discussions_searched_at).where(
                    SilverContent.id == result["content_id"]
                )
            ).first()

        if row:
            has_text = row.text is not None
            has_search = row.discussions_searched_at is not None
            # Self-posts: fully processed when search is done (text was provided by collector)
            # Link posts: fully processed when both text extracted AND search done
            if not result["has_external_url"] and has_search:
                return "skipped"
            if result["has_external_url"] and has_text and has_search:
                return "skipped"

        event = ItemEvent(
            content_id=result["content_id"],
            discussion_id=result["discussion_id"],
            source=source_name,
            domain=result["domain"],
            has_external_url=result["has_external_url"],
        )
        hatchet.event.push("item.new", event.model_dump(), options=PushEventOptions(scope="default"))
        return "emitted"
    except Exception:
        logger.exception("collect.event_emit_error source=%s external_id=%s", source_name, ref["external_id"])
        return "error"
```

**DB query improvement:** The old code did one outerjoin across `SilverDiscussion` + `SilverContent`
to get content_id, domain, text, discussions_searched_at. The new code does one simple query on
`SilverContent` by primary key for just `text` + `discussions_searched_at`. Content-type properties
come from the collector result (no query needed).

### `_upsert_discussion` changes

Currently returns `int | None` (id if new, None if existing). No caller uses the return value.
Change to always return the discussion id:

```python
# Current
if existing:
    return None
return conn.execute(sa.select(...)).scalar()

# New
if existing:
    return existing[0]
return conn.execute(sa.select(...)).scalar()
```

### `collect_source` changes

Passes the result through to `_emit_item_event`, handles `None` for early exits:

```python
with engine.begin() as conn:
    result = collector.process_discussion(ref["raw_data"], conn, ref["source_id"])
count += 1

if hatchet is not None and result is not None:
    emit_result = _emit_item_event(engine, hatchet, ref, name, result)
    if emit_result == "error":
        event_errors += 1
    elif emit_result == "skipped":
        events_skipped += 1
```

### Collector changes

**Return type:** `DiscussionResult | None`. Returns `None` on early exits (validation failures).

**`has_external_url` per collector:**

| Collector | `has_external_url` | Why |
|---|---|---|
| Reddit | `True` for link posts, `False` for self-posts | `is_self` branch |
| HN | `True` when `hit.get("url")`, `False` otherwise | Ask HN / Show HN without URL |
| Lobsters | `True` for link posts, `False` for self-posts | `url != comments_url` check |
| LessWrong | **Always `True`** | Native essays call `ensure_content(page_url)` — webpage pipeline fetches the full page. Does NOT use `_ensure_self_post_content`. |
| Telegram | Always `False`, but `content_id=None` | No `SilverContent` created. Event emission skips on `content_id=None` guard. |
| RSS | Always `True` | Always has a link |
| YouTube | Always `True` | Always external video URL |
| ArXiv | Always `True` | Always points to paper page |
| HuggingFace | Always `True` | Always points to HF paper page |
| GitHub Trending | Always `True` | Always points to repo URL |

**Early exit handling:** Collectors that have early returns (no ext_id, validation failure) return
`None`. Current early returns: HN (no ext_id), Telegram (no text), LessWrong (no ext_id), ArXiv
(no match), HuggingFace (no paper_id), RSS (no external_id).

**Existing-row content_id:** When `_upsert_discussion` returns an existing discussion_id, the
collector's computed `content_id` might differ from the DB's actual `content_id` if the URL changed
between scrapes (no collector includes `content_id` in upsert columns). In practice URLs don't
change between re-scrapes. The spec accepts this low-risk divergence — matching the existing behavior
where `_emit_item_event` queries the current DB state anyway.

## Files Changed

| File | Change |
|------|--------|
| `src/aggre/collectors/base.py` | Add `DiscussionResult`. Change `Collector.process_discussion` return type to `DiscussionResult \| None`. Change `_upsert_discussion` to always return id. |
| `src/aggre/workflows/models.py` | Replace `text_provided` with `has_external_url` on `ItemEvent` |
| `src/aggre/workflows/collection.py` | `_emit_item_event` takes `DiscussionResult`, single DB query for processing state only |
| `src/aggre/workflows/webpage.py` | Filter: `input.has_external_url` instead of `!input.text_provided` |
| `src/aggre/workflows/transcription.py` | Filter: `input.has_external_url` instead of `!input.text_provided` |
| `src/aggre/collectors/hackernews/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/reddit/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/lobsters/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/telegram/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/lesswrong/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/huggingface/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/youtube/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/arxiv/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/rss/collector.py` | Return `DiscussionResult` |
| `src/aggre/collectors/github_trending/collector.py` | Return `DiscussionResult` |
| `tests/workflows/test_collection.py` | Update mocks: `process_discussion` returns `DiscussionResult` |

## Testing

- Update `test_emits_item_new_event`: mock `process_discussion` to return `DiscussionResult` with `has_external_url=True`, verify payload
- Update `test_emits_event_for_self_post`: result has `has_external_url=False`, verify payload
- Update `test_no_event_when_fully_processed`: result has content_id, DB query for dedup still works
- Update `test_no_event_when_content_id_null`: result has `content_id=None`
- Update `test_event_emission_error_doesnt_crash`: pass `DiscussionResult` through
- Existing collector integration tests: verify `process_discussion` returns `DiscussionResult` with expected fields
- Add test: `process_discussion` returns `None` on early exit → no event emitted, no error

## What This Eliminates

- The `text_provided` semantic gap (inferred from DB → authoritative from collector)
- The outerjoin DB query in `_emit_item_event` for content_id/domain (replaced by collector result)

## What This Simplifies

- DB query in `_emit_item_event`: from outerjoin across 2 tables → simple PK lookup for 2 columns

## What This Keeps

- Emission dedup check for processing state (text + discussions_searched_at from DB — correct)
- CANCEL_NEWEST per content_id (race condition safety net)
- Task-level `already_done` guards (last resort)
