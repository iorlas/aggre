# Aggre — Architecture

## Tech Stack

### Core

| Component | Choice | Why |
|-----------|--------|-----|
| **Language** | Python 3.12 (sync) | Great ecosystem. No async — we're rate-limited anyway |
| **Package manager** | uv | Fast, reliable |
| **Database** | **SQLite + WAL mode** | File-based, zero deployment, SQL-queryable. WAL mode for safe concurrent access from fetch + transcribe |
| **ORM/DB layer** | **SQLAlchemy** (Core, not ORM) + Pydantic models | SQLAlchemy Core for schema definition and queries. Pydantic for validation/serialization. No ORM mapping overhead |
| **Migrations** | **Alembic** | Industry standard. Versioned schema changes. Works with SQLAlchemy Core |
| **Config** | **YAML file** | Human-readable, easy to edit source lists |
| **CLI** | **Click** | Lightweight CLI framework |
| **Logging** | **structlog** → file + stdout | Structured JSON logs to file (for persistence), human-readable to stdout (for `docker logs`) |
| **Scheduling** | **Built-in loop mode** | `--loop --interval 3600` flag. Container stays up, docker-compose restart handles crashes |
| **Containerization** | **Docker + docker-compose** | Single image, two services (fetch + transcribe). Volume for DB + config + logs |

### Source-Specific Libraries

| Source | Library | Notes |
|--------|---------|-------|
| **RSS** | `feedparser` | De facto standard, handles all feed formats |
| **Reddit** | `httpx` | HTTP client for Reddit's public JSON API. No auth. Conservative 1 req/3s rate limit |
| **YouTube** | `yt-dlp` | Download videos + extract metadata. No YouTube API key needed |

### Transcription

| Component | Choice | Notes |
|-----------|--------|-------|
| **Engine** | `faster-whisper` | CTranslate2-based, efficient on CPU |
| **Model** | `large-v3` | Best quality. ~10GB RAM, slower than real-time on CPU. Quality is the priority |
| **Workflow** | Fully decoupled from polling | Separate container/process. One video at a time to limit disk usage |

### Reddit Without Auth

Reddit exposes JSON at any URL by appending `.json`:

- `https://www.reddit.com/r/python/hot.json?limit=100`
- `https://www.reddit.com/r/python/new.json?limit=100`
- `https://www.reddit.com/r/python/comments/abc123.json` (for comments)

Anti-blocking strategy:

- Conservative rate limit: **1 req/3s** (well under Reddit's limits)
- Proper `User-Agent` header (Reddit blocks generic agents)
- Exponential backoff on 429/503 responses (using `tenacity`)
- 35 subreddits x 2 sorts = 70 requests → ~3.5 min per poll cycle
- Comment fetching throttled. Full poll cycle with comments may take 15-30 min — acceptable

---

## Dagster Pipeline

All jobs are orchestrated by **Dagster**. The pipeline is structured as independent jobs triggered by schedules and sensors.

### Jobs

| Job | Trigger | Description |
|-----|---------|-------------|
| `collect_job` | Hourly schedule | Fetch discussions from all configured sources. Writes bronze `raw.json`, creates SilverDiscussion + SilverContent rows. |
| `comments_job` | Sensor: discussions need comments | Fetch comments for HN/Reddit/Lobsters discussions. Writes bronze `comments.json`, stores in `comments_json` column. |
| `webpage_job` | Sensor: SilverContent needs downloading | Download HTML for non-YouTube content, extract text with trafilatura. |
| `transcribe_job` | Sensor: YouTube content needs transcription | Download audio, transcribe with faster-whisper, store transcript. Resilient: reuses cached audio/whisper.json. |
| `enrich_job` | Sensor: SilverContent not yet enriched | Search HN/Lobsters for cross-source discussions about collected URLs. |
| `reprocess_job` | Manual trigger | Rebuild silver from bronze `raw.json` files without hitting external APIs. |

### Collector Protocol

Each collector implements two methods:

- `collect_discussions()` — fetch feed from API, write `raw.json` to bronze, return list of `DiscussionRef`
- `process_discussion()` — normalize one bronze discussion into silver rows (SilverContent + SilverDiscussion)

The separation allows `reprocess_job` to call `process_discussion()` alone, reading from bronze without touching APIs.

### Self-Post Handling

Self-posts (Reddit selftext, Ask HN without URL, Lobsters self-posts) create a `SilverContent` row with `text` pre-populated. The content pipeline skips these (null-check pattern: `text IS NOT NULL` means already processed).

### Logging

- **structlog** outputs structured JSON logs
- Dual output: stdout (for `docker logs`) + rotating file in `./data/logs/`
- Each pipeline logs to its own file: `fetch.log`, `transcribe.log`
- Log levels: INFO for normal operations, WARNING for retries/rate limits, ERROR for failures
- `aggre status` CLI command: shows last fetch time per source, transcription queue size, recent errors

---

## Data Model: Medallion Architecture (Bronze + Silver)

Schema managed by Alembic migrations. SQLAlchemy Core for table definitions.

### Layering

```
  Source APIs ──→ Bronze (raw) ──→ Silver (normalized)
                                       ↓
                               Future AI tools (gold, separate project)
```

- **Bronze**: Raw API responses stored as-is. Insurance for reprocessing if parsing logic changes.
- **Silver**: Cleaned, deduplicated, normalized data with structured columns for querying.
- **Gold**: Deferred to future AI projects (summaries, insights, etc.). They'll create their own tables.

### Pipeline Flow

1. Collector fetches from source API
2. Raw response stored in `raw_items` (bronze)
3. Response parsed/normalized → stored in `content_items` (silver)
4. Steps 2-3 happen in the same transaction (no separate reprocessing step for initial load)
5. If parsing logic changes later, we can reprocess: read from `raw_items`, re-generate `content_items`

### `sources` table (config)

```sql
CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,          -- 'rss', 'reddit', 'youtube'
    name TEXT NOT NULL,          -- human label
    config TEXT NOT NULL,        -- JSON: {url, subreddit, channel_id, ...}
    enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    last_fetched_at TEXT
);
```

### `raw_items` table (Bronze)

```sql
CREATE TABLE raw_items (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL,       -- 'rss', 'reddit', 'youtube'
    external_id TEXT NOT NULL,       -- unique ID from source
    raw_data TEXT NOT NULL,          -- full JSON response from API
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source_type, external_id)
);
```

### `content_items` table (Silver)

```sql
CREATE TABLE content_items (
    id INTEGER PRIMARY KEY,
    source_id INTEGER REFERENCES sources(id),
    raw_item_id INTEGER REFERENCES raw_items(id),
    source_type TEXT NOT NULL,       -- 'rss', 'reddit', 'youtube'
    external_id TEXT NOT NULL,       -- unique ID from source
    title TEXT,
    author TEXT,
    url TEXT,
    content_text TEXT,               -- article body, reddit post text, or youtube transcript
    published_at TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    metadata TEXT,                   -- JSON: source-specific extras
    -- NOTE: Current schema uses null-check pattern instead of status columns.
    -- See docs/semantic-model.md for current schema.
    error TEXT,                      -- error message if processing failed
    detected_language TEXT,
    UNIQUE(source_type, external_id)
);
```

### `raw_comments` table (Bronze - comments)

```sql
CREATE TABLE raw_comments (
    id INTEGER PRIMARY KEY,
    raw_item_id INTEGER REFERENCES raw_items(id),
    external_id TEXT NOT NULL,
    raw_data TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(external_id)
);
```

### `reddit_comments` table (Silver - comments)

```sql
CREATE TABLE reddit_comments (
    id INTEGER PRIMARY KEY,
    content_item_id INTEGER REFERENCES content_items(id),
    raw_comment_id INTEGER REFERENCES raw_comments(id),
    external_id TEXT NOT NULL,
    author TEXT,
    body TEXT,
    score INTEGER,
    parent_id TEXT,
    depth INTEGER,
    created_at TEXT,
    UNIQUE(external_id)
);
```

### Indexes

```sql
-- Bronze
CREATE INDEX idx_raw_source_type ON raw_items(source_type);
CREATE INDEX idx_raw_external ON raw_items(source_type, external_id);

-- Silver
CREATE INDEX idx_content_source_type ON content_items(source_type);
CREATE INDEX idx_content_published ON content_items(published_at);
CREATE INDEX idx_content_source_id ON content_items(source_id);
CREATE INDEX idx_content_external ON content_items(source_type, external_id);
-- NOTE: Current schema uses idx_content_needs_processing with null-check pattern.
-- See docs/semantic-model.md for current indexes.
CREATE INDEX idx_comments_content_item ON reddit_comments(content_item_id);
```

### Why One Silver Table (Not Per-Source)

- Enables cross-source queries ("all content from today")
- `metadata` JSON column holds source-specific fields (reddit score, youtube category, etc.)
- SQLite's `json_extract()` makes the JSON column queryable:
  ```sql
  SELECT * FROM content_items
  WHERE source_type = 'reddit'
  AND json_extract(metadata, '$.subreddit') IN ('rust', 'golang')
  AND date(published_at) = date('now')
  ORDER BY json_extract(metadata, '$.score') DESC;
  ```
- Separate `reddit_comments` table because comments are 1:many and benefit from their own schema
- Bronze is also unified (one `raw_items` table) for simplicity, with `source_type` as discriminator

### SQLite Concurrency

- **WAL mode** enabled on connection (`PRAGMA journal_mode=WAL`)
- Allows concurrent reads + one writer
- Both fetch and transcribe do short write transactions — contention is negligible
- `tenacity` retries on `database is locked` errors (rare but handled)

---

## Project Structure

```
aggre/
├── config.yaml                  # Source configuration
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── alembic.ini                  # Alembic configuration
├── alembic/                     # Migration scripts
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── src/aggre/
│   ├── __init__.py
│   ├── cli.py                   # Click CLI with loop mode support
│   ├── config.py                # Load & validate YAML config
│   ├── db.py                    # SQLAlchemy Core engine, table definitions, WAL mode
│   ├── models.py                # Pydantic models for ContentItem, Source, etc.
│   ├── logging.py               # structlog setup (stdout + file)
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── base.py              # Base collector protocol/ABC
│   │   ├── rss.py               # RSS/Atom feed collector
│   │   ├── reddit.py            # Reddit JSON API collector
│   │   └── youtube.py           # YouTube metadata extraction via yt-dlp
│   └── transcriber.py           # faster-whisper integration
├── tests/
│   ├── ...
```

## CLI Commands

```bash
# Polling
aggre fetch                       # Poll all sources once
aggre fetch --source reddit       # Poll only Reddit
aggre fetch --loop --interval 3600  # Poll every hour, run forever (Docker mode)

# Transcription
aggre transcribe                  # Process all pending videos, then exit
aggre transcribe --batch 5        # Process up to 5 videos, then exit
aggre transcribe --loop --interval 900  # Check every 15 min (Docker mode)

# Backfill
aggre backfill youtube            # Fetch full video history for all YouTube channels

# Status
aggre status                      # Show last fetch times, queue sizes, recent errors

# Database
aggre db upgrade                  # Run pending Alembic migrations
aggre db init                     # Initialize DB + run all migrations
```

## Config File (`config.yaml`)

```yaml
rss:
  - name: "Rust Blog"
    url: "https://blog.rust-lang.org/feed.xml"
  - name: "Simon Willison"
    url: "https://simonwillison.net/atom/everything/"

reddit:
  - subreddit: "rust"
  - subreddit: "golang"
  - subreddit: "machinelearning"

youtube:
  - channel_id: "UC_x5XG1OV2P6uZZ5FSM9Ttw"
    name: "Google Developers"
  - channel_id: "UCsBjURrPoezykLs9EqgamOA"
    name: "Fireship"

settings:
  db_path: "./data/aggre.db"
  log_dir: "./data/logs"
  youtube_temp_dir: "./data/tmp/videos"
  whisper_model: "large-v3"
  whisper_model_cache: "./data/models"
  reddit_rate_limit: 3.0
  fetch_limit: 100
```

---

## Dependencies

```toml
dependencies = [
    "structlog>=23.0.0",
    "pydantic>=2.12.2",
    "python-dotenv>=1.2.1",
    "tenacity>=9.1.2",
    "feedparser>=6.0",
    "httpx>=0.28",
    "yt-dlp>=2024.0",
    "faster-whisper>=1.0",
    "click>=8.1",
    "pyyaml>=6.0",
    "sqlalchemy>=2.0",
    "alembic>=1.15",
]
```

---

## Open Considerations

1. **Whisper large-v3 on CPU**: ~10GB RAM, significantly slower than real-time. A 10-minute video might take 30+ minutes to transcribe. For backfill of 15 channels, initial load could take weeks. This is fine — the decoupled architecture means polling is never affected.

2. **YouTube channel listing via yt-dlp**: No YouTube API key needed. `yt-dlp` can enumerate all videos in a channel. Slower than the official API but zero-config.

3. **SQLite concurrency**: WAL mode + short transactions + tenacity retries = safe. If it ever becomes an issue, we can switch to process-level locking or separate DBs, but this is unlikely at this scale.

4. **Whisper model caching**: The large-v3 model (~3GB download) should be cached in the volume (`./data/models/`) so it persists across container restarts and isn't re-downloaded each time.

5. **Reddit API changes**: Reddit has been tightening unauthenticated access. If JSON endpoints stop working, we may need to switch to API credentials later. The collector abstraction makes this a localized change.
