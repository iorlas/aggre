# Medallion Architecture Guidelines

Opinionated guidelines for data layer separation. No code examples — consumed by coding agents.

## Layers

- **Bronze**: raw external data. Filesystem. Immutable — never modify after write.
- **Silver**: transformed, normalized, queryable. PostgreSQL. No raw data here.
- **Gold**: TBD per-project (enriched/final output). Not defined yet.

## Bronze Storage

Two patterns based on access characteristics.

### Directory-per-item (default)

Item-scoped artifacts with few files per item. API JSON, HTML, media, transcriptions.

Path: `data/bronze/{source_type}/{external_id}/{artifact_type}.{ext}`

One directory per item. All raw artifacts for an item live together.

What goes here:
- API response JSON
- Raw HTML
- Whisper JSON (full segments + timestamps)
- Media files (video, audio, images)
- Comments JSON

Scale <100k items: directory scan with mtime filter for discovery.
Scale 100k+: append-only JSONL index file (`_index.jsonl`) alongside the data.

### SQLite (high-volume content-addressed)

For caches with millions of entries where point-lookups by hash key are the primary access pattern. LLM call cache, any content-addressed lookup.

Path: `data/bronze/{cache_name}.sqlite`

- Concurrency: WAL mode — multiple readers + one writer. Write contention is low (LLM calls take seconds, inserts take milliseconds).
- Why not per-call files: millions of micro-files create filesystem pressure, slow directory listing.
- Why SQLite over DuckDB: DuckDB is column-oriented for analytics, wrong for point lookups by hash. Worse concurrency (single writer model).
- Why SQLite over HTTP cache libs: LLM cache is at semantic level (hash of model+prompt+params), not HTTP level. HTTP cache libs (`hishel`, `requests-cache`) are for web fetching, separate concern.
- Why SQLite over JSONL: O(1) lookups vs O(n). Cache needs fast "do I have this?" checks.

### When to use which

- Item-scoped artifacts (API responses, HTML, media, transcriptions): directory-per-item.
- Content-addressed cache with millions of entries: SQLite.

## Silver Storage

PostgreSQL. Transformed, normalized, queryable.

No raw data blobs in silver. Specifically:
- Move `raw_html` out — store in bronze, not silver.
- Store raw Whisper JSON in bronze, not as `body_text`.
- `body_text` holds final extracted/transcribed text only.

Status tracking lives here (`fetch_status`, `transcription_status`). This is silver's own state, not bronze's concern.

## Layer Isolation

Reframed as four rules:

- **Data placement**: raw external data → bronze, regardless of which layer triggered the fetch.
- **Code dependency**: no upward imports. Bronze code never imports silver code, silver never imports gold.
- **Write rule**: any layer can append to bronze (it's an append-only raw data store). Each layer writes to itself normally.
- **Read rule**: reads go downward or same-layer, never upward.
  - Same-layer reads always OK: silver→silver (table joins), gold→gold (aggregates combining aggregates).
  - Downward reads always OK: silver→bronze, gold→silver.
  - Upward reads never: bronze→silver, silver→gold.

This makes LLM cache writes and enrichment fetches during silver transforms idiomatic — the data is raw/external, so it belongs in bronze, even when triggered from silver code.

## Prescriptive Examples

### Transcriptions (Whisper)

- Bronze: `data/bronze/youtube/{video_id}/whisper.json` — full Whisper output with segments, timestamps, confidence scores, detected language, model version.
- Bronze: `data/bronze/youtube/{video_id}/audio.opus` — keep raw audio. Videos get taken down; re-download is slow or impossible.
- Silver: `body_text` = concatenated segment text only. `detected_language` = language code. Timestamps available in bronze when needed.
- Rule: always preserve max-fidelity raw output in bronze. Silver extracts only what's needed for queries.

### LLM Invocations

- Bronze: `data/bronze/llm_cache.sqlite` — content-addressed SQLite cache.
- Key = hash(model + system_prompt + user_prompt + temperature + other params).
- Value = full request JSON + response JSON + metadata (timestamp, token counts).
- Append-only (immutable bronze), WAL mode for concurrency.
- Cache semantics: same input → cached output. Model or prompt change → new hash → cache miss. Saves tokens on re-runs.
- LLM wrapper writes bronze inline during silver transforms — pragmatic, documented pattern. The data is raw external output, so it belongs in bronze regardless of triggering layer.

### TTL / Freshness

- Default: overwrite on re-fetch (latest version wins, simpler).
- Optional: append-only versioning when tracking how content changes over time (architect decides per use case).
- Re-fetchable content (HTML, API responses): silver decides when stale and triggers re-fetch.
- Derived artifacts (Whisper, LLM): invalidated by key change — model/params are part of the hash. Old entries cleaned by periodic GC.
- Freshness is a silver-layer scheduling concern, not bronze's responsibility.

### Bronze-Aware Wrappers

Rule: **never call an external service directly. All external calls go through a bronze-aware wrapper.**

Pattern (read-through cache):
1. Check bronze → if hit, return cached result.
2. If miss → call external service, write result to bronze, return result.

Properties:
- Transparent to calling code — caller doesn't know about caching.
- Wrapper owns both the bronze read/write and the external call.
- Same wrapper works for both separate and combined pipes.
- The wrapper is the "bronze interface" — all bronze access for that data type goes through it.
- Guidelines prescribe the pattern, not the mechanism — coding agent picks decorator, adapter, context manager, or explicit check based on the client's interface.

### Python-Specific Wrapper Prescriptions

Five categories based on library type. Each prescribes the interception point, bronze storage, what to persist, and pitfalls.

**HTTP API clients (httpx)** — you control the HTTP call.

Interception: wrap the `client.get()`/`client.post()` call. Check bronze before calling, write bronze after.

Bronze: directory-per-item. Raw JSON/HTML response body. Not headers, not status codes.

Two keying strategies:

- **Item-keyed** (fetching a known item: HN item by ID, Reddit post by ID): key = `(source_type, external_id)`. Simple, preferred when an external ID exists.
- **Request-keyed** (fetching arbitrary URLs: article HTML, RSS feed listing, enrichment searches): key = hash of semantically significant request fields. Include: HTTP method, normalized URL, significant query params, body (if POST). Exclude: auth tokens, session cookies, tracking params, cache-busting params — these don't affect response content.

URL normalization before keying: strip tracking params (`utm_*`), normalize scheme/host case, sort query params. The caller decides which params are significant — the wrapper just takes a key.

Bronze path for request-keyed calls: `data/bronze/{source_type}/{url_hash}/response.{ext}` (hash because URLs are too long/messy for directory names).

Retry logic (tenacity) goes inside the wrapper, not outside. A successful retry writes to bronze.

Why not hishel/requests-cache: bronze is at semantic level (item/request-keyed with exclusions), not HTTP transport level (verbatim URL). Same URL returns different data over time; auth params change without affecting content.

**Download tools (yt-dlp, any media downloader)** — produce files on disk.

Interception: check if bronze path exists → skip download if yes → download to bronze path directly if no.

Bronze: directory-per-item. `data/bronze/{source_type}/{external_id}/audio.opus`.

**Never delete downloaded media after processing.** Bronze is immutable. Videos get taken down; re-download is slow or impossible.

Download directly to bronze path, not temp dir. Eliminates orphaned temp files.

Partial downloads: atomic write (`{path}.tmp`, rename on completion). `.tmp` = cache miss.

Persist only the final format after conversion (e.g., opus after ffmpeg, not the intermediate webm).

**Local ML inference (faster-whisper, any local model)** — not external, but expensive + raw output is richer than silver needs.

Interception: check if bronze output exists → read if yes → run inference + write to bronze if no.

Bronze: directory-per-item. Full model output at `data/bronze/{source_type}/{external_id}/whisper.json`.

**Persist everything the model returns.** Segments, timestamps, confidence, language, model version, params. Silver extracts only what it needs.

Cache key: `(input_file_hash, model_name, model_params)`. Model change → cache miss.

Why cache local inference: takes minutes, full output has info silver discards but gold may need, re-runs after code changes should be free.

Pitfall: caching only the silver-extracted subset loses segments/timestamps permanently.

**LLM API clients (openai, anthropic)** — content-addressed cache, maps to existing LLM Invocations prescription.

Interception: hash inputs → check SQLite → call API on miss → write on miss.

Bronze: SQLite. `data/bronze/llm_cache.sqlite`. Key = `hash(model + prompt + params)`.

Persist full request + response JSON + metadata. Not just the extracted answer.

Hash only deterministic inputs (not timestamp, request ID).

Prompt change → cache miss. Include full prompt text in hash, not a prompt name.

**Opaque client libraries (feedparser, telethon, any lib that manages its own I/O)** — you don't control the transport.

Interception: **post-call.** Let the library fetch. Immediately persist output to bronze.

No pre-call cache check by default — you can't skip the library's internal fetch. Opt into pre-call check only for immutable sources.

Bronze: directory-per-item. Store parsed output as JSON.

Accept the redundant fetch. If skipping fetches matters, switch to a controllable HTTP client.

Pitfall: serializing opaque library objects. Extract a plain dict before bronze write.

Why not replace feedparser's HTTP: tightly coupled (redirects, encoding, format negotiation). Cost of redundant fetches is low.

**What does NOT need a wrapper** — pure-function libraries (trafilatura, json parsing, text processing). Deterministic, fast, no external data. No bronze involvement.

## Workers

Two valid patterns. Both **always use bronze as intermediary** — the difference is orchestration, not whether bronze is used.

### Separate pipes (expensive sources)

Independent workers, sensor-triggered.

1. **Fetch worker**: external → bronze (independent process).
2. **Transform worker**: bronze → silver (triggered by sensor, separate process).

Better for: rate-limited APIs, slow downloads, LLM $$$, deletable content. Better observability, independent retries, natural decoupling.

### Combined pipe (cheap sources)

One worker, bronze-checked.

1. Check bronze → if data exists, skip fetch, read from bronze.
2. If missing → fetch from external → write to bronze.
3. Read from bronze → transform → write to silver.

Better for: fast HTTP GETs, freely re-fetchable content. Simpler orchestration, fewer moving parts. Still preserves "cache once, reprocess whenever" — re-runs after code changes skip fetch, read existing bronze.

### LLM calls

Naturally combined — LLM wrapper checks cache → miss: call API + cache → hit: return cached. The prompt depends on silver context, so separation would be artificial. Uses bronze-aware wrapper pattern.

### Which to use

- **Expensive source** (rate-limited API, slow download, LLM $$$, deletable content): separate pipes.
- **Cheap source** (fast HTTP GET, freely re-fetchable): combined pipe.
- Key: **combined pipe ≠ skip bronze**. Bronze is always checked first.

## Discovery & Orchestration

### Incremental processing: Dagster sensor + cursor

- **Sensor**: lightweight (<5 sec). Detects changes, yields `RunRequest`. No heavy processing here.
- **Processing**: in ops/jobs, triggered by sensor. All heavy work here.
- **Cursor**: `context.cursor` — stored by Dagster automatically. Tracks last processed position.
- **Bronze→Silver sensor**: watches filesystem. Cursor = mtime or index offset.
- **Silver→Gold sensor**: watches DB. Cursor = max ID or timestamp.
- New items during processing: caught by next sensor tick.

### Batch/backfill

Scheduled asset materialization. Reprocess all historical data.

## Decision Tree

- Incremental/streaming? → sensor + cursor
- Batch/backfill? → scheduled materialization
- Bronze→Silver discovery? → sensor scans filesystem
- Silver→Gold discovery? → sensor queries DB
- Scale <100k? → directory scan + mtime
- Scale 100k+? → JSONL index file
- Per-item parallelism <100k? → dynamic partitions
- Per-item parallelism 100k+? → task queue (Celery, SQS)
- Content-addressed cache? → SQLite in bronze
- Item-scoped artifact? → directory-per-item
- Expensive source? → separate fetch/transform pipes
- Cheap source? → combined pipe (bronze-checked)
- HTTP client you control? → HTTP API client wrapper (pre-call check)
- Tool that downloads files? → download tool wrapper (bronze path, never delete)
- Local ML inference? → ML inference wrapper (full output to bronze)
- Cloud LLM API? → LLM wrapper (content-addressed SQLite)
- Library manages its own I/O? → opaque client wrapper (post-call persist)
- Pure local transform? → no wrapper needed

## What to Avoid and Why

- **`AutomationCondition.eager()` for incremental**: no checkpoints, cold-start issue, no delta detection.
- **Per-item dynamic partitions at millions**: Dagster metadata DB bloats, UI unusable.
- **Processing inside sensors**: blocks daemon, no retry, misses events.
- **Bronze writing to silver**: upward coupling, violates layer isolation.
- **Raw blobs in PostgreSQL**: DB bloat, hard to inspect, filesystem is better.
- **Time-based partitions for non-time-oriented data**: forced abstraction, partition switching confusion.
- **Millions of micro-files in bronze**: use SQLite for content-addressed data instead.
- **Combined pipe that skips bronze**: always check/write bronze, even in combined flow. Bronze is never optional.
- **DuckDB for point-lookup caches**: column-oriented, wrong tool for key-value lookups. Use SQLite.
- **Direct external service calls**: always go through a bronze-aware wrapper for cacheability and auditability.
- **Deleting downloaded media after processing**: bronze is immutable. Keep audio/video permanently. Re-download is slow or impossible (content gets taken down).
- **Caching only silver-extracted subset of ML output**: always cache full model output (segments, timestamps, confidence). Information loss is permanent — gold may need what silver discards.
- **Wrapping pure-function libraries with bronze logic**: libraries like trafilatura, json parsing, text processing are deterministic and fast. No external data, no caching needed. Over-engineering.
- **HTTP-level caches (hishel, requests-cache) as bronze replacement**: bronze is semantic (item-keyed with exclusions), not HTTP transport (verbatim URL-keyed). Same URL returns different data over time; auth params change without affecting content. Use bronze-aware wrappers instead.

## Terms Mapping

- Bronze = raw cache / lake layer
- Silver = warehouse / cleaned layer
- Streaming = sensor polling (near-real-time, ~30s–5min intervals)
- Microbatch = one sensor tick's worth of items
- Backfill = batch re-materialization of historical data
- Checkpoint = cursor position (last processed item)
- Catalog = what exists in bronze (dir listing or JSONL index)

## Maintaining These Guidelines

Rules for adjusting this document.

### Audience

Coding agents, not humans. No code examples — agents generate code from patterns described here. Prescribe the **pattern**, not the **mechanism** (e.g., "use a bronze-aware wrapper" not "use this decorator").

### Prescriptive over descriptive

State what to do and when. Don't just describe options — give decision criteria and a clear default. Every section should answer "what do I pick?" not "what exists?".

### Rationale is mandatory

Every prescription includes **why** and **why not alternatives**. Format: "Why not X: reason." This prevents re-litigating settled decisions and helps agents make correct tradeoffs in edge cases.

### Reframe, don't patch

When reality shows a rule is too strict, **reframe the rule** to match the actual invariant — don't add exceptions. Example: "silver never writes bronze" was too strict; reframed to "any layer can append to bronze, because bronze is defined by data characteristics (raw, external), not by who writes." The reframed rule is simpler and covers more cases.

### Ground with examples

Abstract rules get a **Prescriptive Examples** entry showing concrete bronze paths, silver fields, and the reasoning. Examples are the primary way agents understand intent.

### Decision tree as index

Every new pattern or either/or choice gets a Decision Tree entry. The decision tree is how agents route to the right pattern without reading every section. Format: `condition? → prescription`.

### Anti-patterns pull their weight

"What to Avoid" entries must state **why it's bad** and **what to do instead**. Don't list something as anti-pattern if there's no concrete alternative.

### Keep it flat

No nested sub-sub-sections. Each section is a self-contained reference. Agents scan by heading, not by reading top-to-bottom.

## References

- Dagster sensors: docs.dagster.io — sensors concept
- Dagster dynamic partitions: docs.dagster.io — partitioning assets
- Dagster IO Managers: docs.dagster.io — IO management
- Observable source assets: docs.dagster.io — asset observations
