# TODO

Deferred from the architecture brainstorming session (2026-02-23).

## 1. Rename SilverDiscussion

**Question:** Should `SilverDiscussion` be renamed to `SilverObservation` or `SilverReference`?

**Context:** A "discussion" on HN/Reddit/Lobsters is literally a discussion thread. But an RSS entry, a Telegram message, or a HuggingFace paper listing isn't really a "discussion" — it's more of an observation or reference to content. The current name biases thinking toward forum-style sources and feels wrong for RSS/Telegram/HuggingFace rows.

**Trade-offs:**
- Rename touches every file in the codebase (db.py, all collectors, all tests, all docs, migration)
- `SilverObservation` is more accurate but less intuitive for new readers
- `SilverReference` implies a link to content, but some rows have no `content_id` (self-posts)
- The current name works fine in practice — everyone understands what it means

**Decision criteria:** Do it if we're already doing a major migration. Don't do it standalone — the churn isn't worth a naming improvement alone.

## 2. Per-domain settings classes

**Question:** When should `Settings` be split into per-domain settings classes (e.g., `TranscriptionSettings`, `ContentSettings`)?

**Context:** `src/aggre/settings.py` is ~30 lines with flat fields: `database_url`, `proxy_url`, `log_dir`, `bronze_dir`, rate limits per source, `whisper_model`, `whisper_device`, `max_video_size_mb`. These span multiple domains (transcription, HTTP, storage) but are all in one class.

**Trade-offs:**
- Current flat structure works — the file is small and rarely changes
- Per-domain classes prevent merge conflicts when two agents touch settings simultaneously
- Per-domain classes add import complexity (which settings class does this module need?)
- python-guidelines.md says: "split when settings actually grow per-domain"

**Decision criteria:** Split when any domain accumulates 3+ settings fields. Currently transcription has 3 (`whisper_model`, `whisper_device`, `max_video_size_mb`) — borderline. Trigger: adding a 4th transcription setting.

## 3. Move telegram-auth CLI to collectors/telegram/

**Question:** Should the `telegram-auth` CLI command move from `src/aggre/cli.py` to `src/aggre/collectors/telegram/`?

**Context:** `cli.py` exists solely for the `telegram-auth` command — a one-time interactive setup that generates a Telegram session string. It's the only CLI command. It imports Telethon and runs async code. The collector itself is in `collectors/telegram/`.

**Trade-offs:**
- Moving it co-locates auth with the collector that uses it (domain alignment)
- But `cli.py` is the composition root (layer 3) — it's allowed to import from everywhere
- Moving auth logic into a layer-2 collector package would violate the dependency layer rule if it needs config/settings
- The command is run once per deployment, not a maintenance burden
- If more CLI commands are added later, having a central `cli.py` makes sense

**Decision criteria:** Move it only if `cli.py` grows to 3+ commands that are all collector-specific. If general CLI commands are added (e.g., `aggre validate-config`), keep `cli.py` as the central entry point.

## 4. Track which source discovered content first

**Question:** Should `SilverContent` track which source/discussion first created it?

**Context:** When multiple sources discuss the same URL, the first collector to call `ensure_content()` creates the `SilverContent` row. Currently there's no record of who created it — only `created_at` timestamp. Enrichment later finds cross-source discussions, but we can't answer "where did we first see this URL?"

**Trade-offs:**
- Adding `discovered_by_source_type` and `discovered_by_discussion_id` to SilverContent adds two columns
- Useful for analytics: "what percentage of content is first discovered via RSS vs HN?"
- Useful for pipeline debugging: "why does this content exist?"
- Adds complexity to `ensure_content()` — the ON CONFLICT DO NOTHING pattern means the first inserter wins, but we'd need RETURNING to capture whether this was a new insert
- The `created_at` + join to earliest `SilverDiscussion.fetched_at` gives an approximation already

**Decision criteria:** Implement when building a dashboard or analytics layer. Not needed for core pipeline operation.

## 5. Pipeline overview format: ASCII vs Mermaid

**Question:** Should architecture diagrams use ASCII art or Mermaid?

**Context:** `.planning/codebase/ARCHITECTURE.md` currently uses ASCII art for the pipeline overview. Mermaid would render in GitHub/GitLab but is less readable in terminals and raw markdown.

**Trade-offs:**
- ASCII: works everywhere (terminal, raw file, any markdown renderer), easy for AI agents to read and modify
- Mermaid: renders as proper diagrams on GitHub, supports more complex layouts, but agents can't "see" the rendered output
- The audience for these docs is AI coding agents (per guidelines), not humans browsing GitHub

**Decision criteria:** Stick with ASCII. The docs are agent-consumed, and ASCII is universally parseable. Consider Mermaid only if docs are published to a human-facing site.
