# TODO

## Deferred Architecture Questions

Deferred from the architecture brainstorming session (2026-02-23).

### 1. Rename SilverDiscussion

**Question:** Should `SilverDiscussion` be renamed to `SilverObservation` or `SilverReference`?

**Context:** A "discussion" on HN/Reddit/Lobsters is literally a discussion thread. But an RSS entry, a Telegram message, or a HuggingFace paper listing isn't really a "discussion" — it's more of an observation or reference to content. The current name biases thinking toward forum-style sources and feels wrong for RSS/Telegram/HuggingFace rows.

**Trade-offs:**
- Rename touches every file in the codebase (db.py, all collectors, all tests, all docs, migration)
- `SilverObservation` is more accurate but less intuitive for new readers
- `SilverReference` implies a link to content, but some rows have no `content_id` (self-posts)
- The current name works fine in practice — everyone understands what it means

**Decision criteria:** Do it if we're already doing a major migration. Don't do it standalone — the churn isn't worth a naming improvement alone.

### 2. Per-domain settings classes

**Question:** When should `Settings` be split into per-domain settings classes (e.g., `TranscriptionSettings`, `ContentSettings`)?

**Context:** `src/aggre/settings.py` is ~30 lines with flat fields: `database_url`, `proxy_url`, `log_dir`, `bronze_dir`, rate limits per source, `whisper_model`, `whisper_device`, `max_video_size_mb`. These span multiple domains (transcription, HTTP, storage) but are all in one class.

**Trade-offs:**
- Current flat structure works — the file is small and rarely changes
- Per-domain classes prevent merge conflicts when two agents touch settings simultaneously
- Per-domain classes add import complexity (which settings class does this module need?)
- python-guidelines.md says: "split when settings actually grow per-domain"

**Decision criteria:** Split when any domain accumulates 3+ settings fields. Currently transcription has 3 (`whisper_model`, `whisper_device`, `max_video_size_mb`) — borderline. Trigger: adding a 4th transcription setting.

### 3. Move telegram-auth CLI to collectors/telegram/

**Question:** Should the `telegram-auth` CLI command move from `src/aggre/cli.py` to `src/aggre/collectors/telegram/`?

**Context:** `cli.py` exists solely for the `telegram-auth` command — a one-time interactive setup that generates a Telegram session string. It's the only CLI command. It imports Telethon and runs async code. The collector itself is in `collectors/telegram/`.

**Trade-offs:**
- Moving it co-locates auth with the collector that uses it (domain alignment)
- But `cli.py` is the composition root (layer 3) — it's allowed to import from everywhere
- Moving auth logic into a layer-2 collector package would violate the dependency layer rule if it needs config/settings
- The command is run once per deployment, not a maintenance burden
- If more CLI commands are added later, having a central `cli.py` makes sense

**Decision criteria:** Move it only if `cli.py` grows to 3+ commands that are all collector-specific. If general CLI commands are added (e.g., `aggre validate-config`), keep `cli.py` as the central entry point.

### 4. Track which source discovered content first

**Question:** Should `SilverContent` track which source/discussion first created it?

**Context:** When multiple sources discuss the same URL, the first collector to call `ensure_content()` creates the `SilverContent` row. Currently there's no record of who created it — only `created_at` timestamp. Enrichment later finds cross-source discussions, but we can't answer "where did we first see this URL?"

**Trade-offs:**
- Adding `discovered_by_source_type` and `discovered_by_discussion_id` to SilverContent adds two columns
- Useful for analytics: "what percentage of content is first discovered via RSS vs HN?"
- Useful for pipeline debugging: "why does this content exist?"
- Adds complexity to `ensure_content()` — the ON CONFLICT DO NOTHING pattern means the first inserter wins, but we'd need RETURNING to capture whether this was a new insert
- The `created_at` + join to earliest `SilverDiscussion.fetched_at` gives an approximation already

**Decision criteria:** Implement when building a dashboard or analytics layer. Not needed for core pipeline operation.

### 5. Pipeline overview format: ASCII vs Mermaid

**Question:** Should architecture diagrams use ASCII art or Mermaid?

**Context:** `.planning/codebase/ARCHITECTURE.md` currently uses ASCII art for the pipeline overview. Mermaid would render in GitHub/GitLab but is less readable in terminals and raw markdown.

**Trade-offs:**
- ASCII: works everywhere (terminal, raw file, any markdown renderer), easy for AI agents to read and modify
- Mermaid: renders as proper diagrams on GitHub, supports more complex layouts, but agents can't "see" the rendered output
- The audience for these docs is AI coding agents (per guidelines), not humans browsing GitHub

**Decision criteria:** Stick with ASCII. The docs are agent-consumed, and ASCII is universally parseable. Consider Mermaid only if docs are published to a human-facing site.

### 6. Priority-ordered comment fetching

**Goal:** Fetch comments for high-score discussions first, while still eventually fetching all.

**Context:** Reddit ingests ~600 discussions/day. Even with proxy rotation and 12 max_runs, there's a throughput ceiling. High-score posts have time-sensitive discussions that go stale. Low-score posts can wait hours without losing value.

**Approach options:**
1. **Two-tier workflows** — split into `process-comments-priority` (score > threshold, more workers) and `process-comments-normal`. Collection emits to different events based on score. Simple but rigid threshold.
2. **Score-ordered batch poll** — replace event-driven model with periodic query: `WHERE comments_json IS NULL ORDER BY score DESC`. Loses event-driven reactivity.
3. **Score in event payload + CEL-based concurrency groups** — add `score` to `SilverContentRef`, use Hatchet CEL filter to split high/low into different concurrency groups with different `max_runs`. Keeps event-driven model, adjusts throughput allocation. Cleanest option.

**Prerequisite:** Add `score` and `comment_count` to `SilverContentRef` event payload in `workflows/models.py` and `workflows/collection.py`.

**Decision criteria:** Implement when comment backlog persists despite proxy rotation, or when gold-layer consumers need fresh high-signal comments.

## Open Items

### S3/Garage Remote Performance

- [ ] **I5: Redundant bronze writes every collection run** — All collectors via `BaseCollector._write_bronze()` in `src/aggre/collectors/base.py:79-81`. Every item written to S3 every run, even if unchanged. ~4,320 unnecessary PUTs/day for HN alone. Fix: only write for genuinely new items (use DB upsert result).

- [ ] **M2: `bronze_path()` misleading for S3** — `src/aggre/utils/bronze.py:160-182`. Returns local path that doesn't exist on S3 backend. Fix: deprecate or assert on S3 backend.

- [ ] **M3: No S3 connection health check at startup** — If Tailscale/Garage down, first S3 op fails with opaque error. Fix: add health check in Dagster resource init.

### Hatchet Smoke Test Issues (2026-03-07)

- [ ] **H4: Hatchet token lifecycle** — Token is manually generated and stored in `.env`. Expires after ~3 months (JWT exp). No automated rotation. Document the token generation process and consider automating it in `make dev-remote` setup.

### Event-Driven Migration (2026-03-08)

- [ ] **E1: Router optimization** — If skip runs become noisy in Hatchet UI (e.g. `process-transcription` skipping non-YouTube items), add a router workflow that dispatches to the correct downstream workflow instead of broadcasting `item.new` to all subscribers.

- [ ] **E2: Hatchet data retention** — Verify/configure retention on Hatchet server. Old workflow runs accumulate in Postgres. Check `HATCHET_RETENTION_PERIOD` env var or equivalent.

- [ ] **E3: Remove StageTracking** — `src/aggre/tracking/` module is no longer used by workflows but kept for Grafana dashboards. After dashboards are migrated to Hatchet OLAP tables, remove the module, DB table, and related alembic migration. **Pre-requisites:** (a) set `SilverContent.enriched_at` in `search_one()` so discussion search coverage is queryable without StageTracking; (b) catch final-retry failures in Hatchet task wrappers and write `SilverContent.error` / `SilverDiscussion.error` so permanently-failed items don't stay in "pending" (`text IS NULL AND error IS NULL`) state; (c) migrate Grafana dashboards to Hatchet OLAP tables.

- [ ] **E4: Backfill CLI** — Need a way to trigger per-item workflows for existing unprocessed content (replaces old batch functions). E.g. `python -m aggre.backfill webpage` queries DB for unprocessed content and emits `item.new` events.

- [ ] **E5: Comment events for discussions without content** — `_emit_item_event` skips discussions without `content_id` (e.g. Ask HN, Telegram messages). These items never get comment-fetching via the event-driven path. Either emit a separate event for comment-only items or keep a lightweight cron-based comments fallback.

- [ ] **E6: `_COMMENT_SOURCES` hardcoded** — If a new collector adds `fetch_discussion_comments()`, the tuple in `comments.py:24` must be manually updated. Consider a dynamic check or a test that verifies all collectors with that method are listed.

- [ ] **E7: DB query failure path in `_emit_item_event` untested** — The `try/except` at `collection.py:93` catches both DB query and `hatchet.event.push` failures. Only the push failure path has a dedicated test. Same except clause, so functionally covered, but no explicit DB-failure test.
