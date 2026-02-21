# Medallion Architecture Guidelines

Opinionated guidelines for data layer separation. No code examples — consumed by coding agents.

## Layers

- **Bronze**: raw external data. Filesystem. Immutable — never modify after write.
- **Silver**: transformed, normalized, queryable. PostgreSQL. No raw data here.
- **Gold**: TBD per-project (enriched/final output). Not defined yet.

## Bronze Storage

Filesystem (or S3). Not database.

Naming: `data/bronze/{source_type}/{external_id}/{artifact_type}.{ext}`

One directory per item. All raw artifacts for an item live together.

What goes here:
- API response JSON
- Raw HTML
- Whisper JSON (full segments + timestamps)
- LLM prompt + response pairs
- Media files (video, audio, images)
- Comments JSON

Scale <100k items: directory scan with mtime filter for discovery.
Scale 100k+: append-only JSONL index file (`_index.jsonl`) alongside the data.

## Silver Storage

PostgreSQL. Transformed, normalized, queryable.

No raw data blobs in silver. Specifically:
- Move `raw_html` out — store in bronze, not silver.
- Store raw Whisper JSON in bronze, not as `body_text`.
- `body_text` holds final extracted/transcribed text only.

Status tracking lives here (`fetch_status`, `transcription_status`). This is silver's own state, not bronze's concern.

Silver reads from bronze, never writes to bronze.

## Layer Isolation

Each layer reads from below, writes to itself.

Bronze knows nothing about silver or gold. No upward dependencies. Data flows down only.

## Workers

Split into: **fetch worker** (writes bronze) and **transform worker** (reads bronze, writes silver).

- **Fetch worker**: gets external data, writes raw to bronze filesystem. Done. Knows nothing about silver.
- **Transform worker**: reads bronze, transforms, writes silver.

Never combine fetch + transform in one worker. Current code violates this — migration needed.

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

## What to Avoid and Why

- **`AutomationCondition.eager()` for incremental**: no checkpoints, cold-start issue, no delta detection.
- **Per-item dynamic partitions at millions**: Dagster metadata DB bloats, UI unusable.
- **Processing inside sensors**: blocks daemon, no retry, misses events.
- **Bronze writing to silver**: upward coupling, violates layer isolation.
- **Raw blobs in PostgreSQL**: DB bloat, hard to inspect, filesystem is better.
- **Time-based partitions for non-time-oriented data**: forced abstraction, partition switching confusion.

## Terms Mapping

- Bronze = raw cache / lake layer
- Silver = warehouse / cleaned layer
- Streaming = sensor polling (near-real-time, ~30s–5min intervals)
- Microbatch = one sensor tick's worth of items
- Backfill = batch re-materialization of historical data
- Checkpoint = cursor position (last processed item)
- Catalog = what exists in bronze (dir listing or JSONL index)

## References

- Dagster sensors: docs.dagster.io — sensors concept
- Dagster dynamic partitions: docs.dagster.io — partitioning assets
- Dagster IO Managers: docs.dagster.io — IO management
- Observable source assets: docs.dagster.io — asset observations
