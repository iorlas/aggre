# Workflow Redesign: Rich Events & Clean Architecture

## Problem

The current workflow architecture suffers from a thin event contract. Collectors emit `ItemEvent` with only `(content_id, discussion_id, source, domain, text_provided)`. All four downstream workflows subscribe to the same `item.new` event and must query the database to determine "is this for me?" — 99% discover "already done" and skip, but only after consuming a Hatchet scheduler slot. This created a 52K+ task backlog and forced four layers of dedup guardrails (emission-time check, CANCEL_NEWEST, text_provided filter, task-level guards).

Secondary problems:
- Hatchet board is unreadable — noise from skipped runs drowns real failures
- `_emit_item_event` contains emission-time dedup logic that partially duplicates the CEL filters
- Workflow files mix business logic with Hatchet wiring (though this is acceptable)
- No event retention — Hatchet stores events forever, growing the internal database
- Hatchet admin password resets on volume wipe (no fixed seed credential)

## Solution

Enrich the event contract so Hatchet CEL filters route events to the correct workflow at dispatch time. Only relevant items reach each workflow. The board becomes trustworthy — every run represents real work.

### Core Principles

1. **Collector + DB model guide routing** — no derived flags, no config layer. The event mirrors DB columns. Domain knowledge stays in the subscriber's CEL filter.
2. **Event is for routing, DB is for processing** — every workflow re-fetches from the database as its first step. Events may be stale; the database is truth.
3. **Each stage owns its columns exclusively** — no shared mutable columns between concurrent workflows. This guarantees safe concurrent updates without locking (see Concurrency Safety below).
4. **Lean on Hatchet** — use Hatchet-native tracking (run history, retries, errors) instead of custom status columns or logging tables.

## Design

### 1. Event Model: `SilverContentRef`

Replace `ItemEvent` with `SilverContentRef` — a compact reference that mirrors DB columns without derived fields:

```python
class SilverContentRef(BaseModel):
    """Compact reference to a silver_content row for event dispatch.
    Mirrors DB columns — no derived fields.
    Workflows must re-fetch from DB for processing."""

    content_id: int
    discussion_id: int  # required — comments workflow depends on this
    source: str
    domain: str | None = None
    text_provided: bool  # null check on text column — see note below
```

**Why `SilverContentRef`?** The name tells both humans and AI: it's a **reference** to a `SilverContent` entity, not the entity itself. Workflows should never use it as data — always re-fetch.

**`text_provided` is defense-in-depth only.** Due to Layer 1 emission-time dedup (`if disc.text is not None: return "skipped"`), any event that reaches construction will always have `text_provided=False`. The CEL filter `!input.text_provided` is therefore always true at emission time. This is intentional: Layer 1 prevents event volume from growing; `text_provided` in the CEL filter guards against future changes where Layer 1 might be relaxed (e.g., if we want to re-process items with stale text). It costs nothing and adds safety.

**`discussion_id` is required** — the comments workflow directly accesses `input.discussion_id` and would fail if it were `None`. Every emitted event has a discussion context.

### 2. CEL Filter Routing

Each workflow's CEL filter uses real data fields. Domain knowledge lives in the subscriber, not the event:

| Workflow | CEL Filter | Rationale |
|----------|-----------|-----------|
| Webpage | `!input.text_provided && !(input.domain in ['youtube.com', 'youtu.be', 'm.youtube.com', 'v.redd.it', 'i.redd.it'])` | Webpage workflow knows which domains aren't webpages. Items with `domain=null` pass the filter — this is correct (they may have fetchable URLs). The `.pdf` extension check remains in Python code (`SKIP_EXTENSIONS`) since the URL is not in the event. |
| Transcription | `input.domain == 'youtube.com' && !input.text_provided` | Transcriber knows it handles YouTube |
| Comments | `input.source in ['reddit', 'hackernews', 'lobsters']` | Comments workflow knows which sources have comments |
| Discussion search | Receives all events (no filter, or minimal) | **Note:** This workflow does not exist yet — it is out of scope for this redesign. Listed here for completeness of the routing design. When implemented, it would use `SilverContentRef.discussion_id`. |

**What this eliminates:**
- Layer 2 (CANCEL_NEWEST per content_id) — still useful as a safety net, keep it
- Layer 3 (text_provided filter in workflow code) — moved to CEL, no longer in Python
- Layer 4 (task-level "already_done" guards) — keep as idempotency defense, but they rarely fire

### 3. Event Emission (Collection)

`_emit_item_event` simplifies. The emission-time dedup (Layer 1) stays because it's cheap and prevents unnecessary Hatchet event storage. But the logic becomes cleaner:

```python
def _emit_item_event(engine, hatchet, ref, source_name) -> str:
    with engine.connect() as conn:
        disc = conn.execute(
            sa.select(
                SilverDiscussion.id,
                SilverDiscussion.content_id,
                SilverContent.domain,
                SilverContent.text,
            )
            .outerjoin(SilverContent, SilverContent.id == SilverDiscussion.content_id)
            .where(
                SilverDiscussion.source_type == source_name,
                SilverDiscussion.external_id == ref["external_id"],
            )
        ).first()

    if not disc or not disc.content_id:
        return "skipped"

    # Layer 1: skip fully-processed items (cheap emission-time dedup)
    if disc.text is not None:
        return "skipped"

    event = SilverContentRef(
        content_id=disc.content_id,
        discussion_id=disc.id,
        source=source_name,
        domain=disc.domain,
        text_provided=disc.text is not None,
    )
    hatchet.event.push("item.new", event, options=PushEventOptions(scope="default"))
    return "emitted"
```

Changes from current:
- `ItemEvent` → `SilverContentRef`
- Continue using `.model_dump()` for event push (matches current pattern; verify SDK behavior before changing)
- `text_provided` is always `False` after the Layer 1 guard — this is intentional (see Section 1)
- No other logic changes — Layer 1 dedup stays as-is

### 4. Workflow Registration

Each workflow's `register()` function changes minimally — `input_validator=SilverContentRef` instead of `ItemEvent`, and CEL filter expressions may be adjusted to use the new field names. Business logic functions (`download_one`, `extract_one`, `transcribe_one`, etc.) are unchanged.

Example for webpage:

```python
def register(h):
    wf = h.workflow(
        name="process-webpage",
        on_events=["item.new"],
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
        input_validator=SilverContentRef,
        default_filters=[DefaultFilter(expression=_webpage_filter_expr, scope="default")],
    )

    @wf.task(execution_timeout="5m", schedule_timeout="720h", retries=7, backoff_factor=4, backoff_max_seconds=3600)
    def download_task(input: SilverContentRef, ctx) -> StepOutput:
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        result = download_one(engine, cfg, input.content_id)
        ctx.log(f"Download: {result.status} for content_id={input.content_id}")
        return result

    @wf.task(parents=[download_task], execution_timeout="5m", schedule_timeout="720h", retries=7, backoff_factor=4, backoff_max_seconds=3600)
    def extract_task(input: SilverContentRef, ctx) -> StepOutput:
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        result = extract_one(engine, input.content_id)
        ctx.log(f"Extract: {result.status} for content_id={input.content_id}")
        return result

    return wf
```

### 5. Concurrency Safety Constraint

**Rule: Each stage writes only to columns it owns. No shared mutable columns between concurrent workflows.**

Current column ownership:

| Stage | Columns Written | Table |
|-------|----------------|-------|
| Webpage (download+extract) | `text`, `title` | `silver_content` |
| Transcription | `text`, `detected_language`, `transcribed_by` | `silver_content` |
| Comments | `comments_json`, `comments_fetched_at` | `silver_discussions` |
| Discussion search | `content_id` | `silver_discussions` |

**Note:** Webpage and Transcription both write `text` on `silver_content`. This is safe because CEL filters ensure they never process the same item — webpage skips YouTube domains, transcription only processes YouTube. The column ownership is partitioned by domain, not by column name alone.

**Why no locking is needed:** PostgreSQL row-level locks during `UPDATE ... SET col = val WHERE id = X` are held only for the duration of the UPDATE statement. Two concurrent UPDATEs on different columns of the same row serialize briefly but both succeed. As long as no workflow does read-modify-write on a column another workflow also modifies, there is no lost-update risk.

**When this changes:** If a future stage needs to modify a column that another stage also writes (e.g., two stages both update `meta`), either:
- Use `SELECT ... FOR UPDATE` to serialize access
- Move the output to a separate table (Data Vault satellite pattern)

This constraint MUST be documented in `CLAUDE.md` and `docs/guidelines/` so AI agents and humans respect it when adding new stages.

### 6. Hatchet Operational Fixes

#### Fixed admin password

In `docker-compose.prod.yml`, replace `${HATCHET_ADMIN_PASSWORD}` with a hardcoded value:

```yaml
SEED_DEFAULT_ADMIN_EMAIL: "admin@example.com"
SEED_DEFAULT_ADMIN_PASSWORD: "<set-a-fixed-password-here>"
```

On volume wipe, Hatchet re-seeds these credentials automatically. Use only alphanumeric characters and dashes (no `!`, `$`, or other bash-special characters). This is a non-sensitive credential — Hatchet UI is only accessible via Tailscale private network.

#### Event retention

Add to `hatchet-lite` environment in both compose files:

```yaml
# Research needed: verify exact env var name for Hatchet event retention.
# May be DATA_RETENTION_PERIOD, EVENT_RETENTION_DAYS, or configured via API.
# If no native retention exists, add a monthly cleanup cron workflow.
```

This is a research task during implementation — the exact env var needs verification.

#### CLAUDE.md protection

Add to project `CLAUDE.md`:

```
## Hatchet Safety

- Never delete Hatchet volumes or modify Hatchet's internal database tables
- Use Hatchet's REST API for run management (cancel, replay, cleanup)
- Admin credentials are hardcoded in docker-compose — volume wipes auto-recover
```

### 7. Discussion Search (Out of Scope)

A discussion-search workflow does not exist yet. When implemented in a future redesign, it would subscribe to `item.new` events and use `SilverContentRef.discussion_id` for cross-source discovery. Whether it needs a separate `SilverDiscussionRef` model or a separate event type (`discussion.new`) will be decided at that time. This redesign does not create new workflows.

## Files Changed

| File | Change |
|------|--------|
| `src/aggre/workflows/models.py` | Rename `ItemEvent` → `SilverContentRef`, keep other models |
| `src/aggre/workflows/collection.py` | Use `SilverContentRef` in `_emit_item_event` |
| `src/aggre/workflows/webpage.py` | `input_validator=SilverContentRef`, update type hints |
| `src/aggre/workflows/transcription.py` | Same |
| `src/aggre/workflows/comments.py` | Same |
| `src/aggre/workflows/reprocess.py` | Same (if it uses ItemEvent) |
| `docker-compose.prod.yml` | Hardcoded admin password, event retention env var |
| `docker-compose.local.yml` | Event retention env var |
| `CLAUDE.md` | Hatchet safety rules, column ownership constraint |
| `docs/guidelines/semantic-model.md` | Document column ownership per stage |

## What Is NOT Changed

- **Business logic functions** (`download_one`, `extract_one`, `transcribe_one`, etc.) — untouched
- **Collectors** — untouched (they call `_emit_item_event` which changes internally)
- **RSS collection workflow** (`rss_collection.py`) — untouched; it calls `collect_source` which calls `_emit_item_event` internally
- **Database schema** — no migrations, no new tables
- **Hatchet infrastructure** — same containers, same PostgreSQL
- **Layer 1 emission dedup** — stays
- **CANCEL_NEWEST** — stays as safety net
- **Task-level idempotency guards** (`if row.text is not None: return skipped`) — stay

## Testing

- **Existing tests pass unchanged** — business logic functions are tested independently of Hatchet
- **New test: `SilverContentRef` field sync** — assert that `SilverContentRef` fields are a subset of `SilverContent` + `SilverDiscussion` columns (catches schema drift)
- **CEL filter verification** — integration test that emits events with known attributes and asserts only the correct workflow receives them (E2E against hatchet-lite)
- **Diff coverage** — `make coverage-diff` must pass at 95% for changed lines

## Risks

1. **CEL filter syntax errors** — a typo in a filter silently drops events. Mitigated by integration tests.
2. **`text_provided` stale in event** — collector emits `text_provided=false`, then another process fills `text` before the workflow starts. Mitigated by task-level idempotency guard (re-checks DB).
3. **Hatchet event retention** — if no native retention exists, events accumulate forever. Mitigated by researching the exact config during implementation, with fallback to a cleanup cron.
4. **In-flight events during deployment** — if old `ItemEvent`-schema events are queued when the new code deploys with `SilverContentRef` validation, those events may fail. Mitigated by: the field names and types are identical (only the class name changes), so Hatchet validates against the field schema, not the Python class name. If the Hatchet queue is large at deploy time, drain it first by pausing collectors, waiting for workers to clear, then deploying.
