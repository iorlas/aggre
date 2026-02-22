# Codebase Structure

**Analysis Date:** 2026-02-22

## Directory Layout

```
/work/
├── src/aggre/                    # Main package source
│   ├── __init__.py               # Package version
│   ├── cli.py                    # Click CLI (telegram-auth only)
│   ├── config.py                 # YAML config loading + pydantic Settings
│   ├── db.py                     # SQLAlchemy ORM models (Source, SilverDiscussion, SilverContent)
│   ├── statuses.py               # Status enums (FetchStatus, TranscriptionStatus, CommentsStatus)
│   ├── settings.py               # Pydantic settings with env var overrides
│   ├── urls.py                   # URL normalization and SilverContent management
│   ├── content_downloader.py     # HTTP download → bronze (parallel, I/O-bound)
│   ├── content_extractor.py      # Bronze → silver text extraction (trafilatura)
│   ├── transcriber.py            # YouTube video transcription (yt-dlp + faster-whisper)
│   ├── enrichment.py             # Cross-source enrichment (search HN/Lobsters for URLs)
│   ├── utils/                    # Generic reusable helpers (no Aggre-specific logic)
│   │   ├── __init__.py
│   │   ├── bronze.py             # Bronze filesystem writer (write_bronze_json)
│   │   ├── bronze_http.py        # Bronze-aware HTTP wrapper (write_bronze_by_url)
│   │   ├── http.py               # Shared HTTP client factory (httpx)
│   │   └── logging.py            # Structured logging setup (structlog + stdlib)
│   ├── collectors/               # Source-specific collector plugins
│   │   ├── __init__.py           # COLLECTORS registry dict
│   │   ├── base.py               # BaseCollector shared helpers, Collector/SearchableCollector protocols
│   │   ├── hackernews/           # Hacker News collector (Algolia API, searchable)
│   │   ├── reddit/               # Reddit collector (PRAW)
│   │   ├── rss/                  # RSS/Atom collector (feedparser)
│   │   ├── youtube/              # YouTube collector (yt-dlp)
│   │   ├── lobsters/             # Lobsters collector (HTTP API, searchable)
│   │   ├── huggingface/          # HuggingFace Papers collector (HTTP API)
│   │   └── telegram/             # Telegram collector (Telethon async)
│   └── dagster_defs/             # Dagster orchestration definitions
│       ├── __init__.py           # Compose all definitions into dg.Definitions
│       ├── resources.py          # DatabaseResource (ConfigurableResource)
│       ├── collection/           # Collection domain
│       │   ├── __init__.py
│       │   ├── job.py            # collect_job (all sources)
│       │   └── schedule.py       # hourly_collection schedule
│       ├── content/              # Content fetch domain
│       │   ├── __init__.py
│       │   ├── job.py            # content_job (download + extract)
│       │   └── sensor.py         # content_sensor (watches pending downloads)
│       ├── enrichment/           # Enrichment domain
│       │   ├── __init__.py
│       │   ├── job.py            # enrich_job (HN/Lobsters search)
│       │   └── sensor.py         # enrichment_sensor (watches unenriched content)
│       └── transcription/        # Transcription domain
│           ├── __init__.py
│           ├── job.py            # transcribe_job (yt-dlp + whisper)
│           └── sensor.py         # transcription_sensor (watches pending transcriptions)
├── tests/                        # Test suite
│   ├── conftest.py               # pytest fixtures (PostgreSQL test engine, table cleanup)
│   ├── test_urls.py              # URL normalization and ensure_content tests
│   ├── test_content.py           # Content downloader/extractor state transitions
│   ├── test_enrichment.py        # Enrichment pipeline tests
│   ├── test_bronze.py            # Bronze filesystem writer tests
│   ├── test_bronze_http.py       # Bronze HTTP wrapper tests
│   ├── test_hackernews.py        # HackerNews collector tests
│   ├── test_reddit.py            # Reddit collector tests
│   ├── test_rss.py               # RSS collector tests
│   ├── test_youtube.py           # YouTube collector tests
│   ├── test_lobsters.py          # Lobsters collector tests
│   ├── test_huggingface.py       # HuggingFace collector tests
│   ├── test_telegram.py          # Telegram collector tests
│   ├── test_acceptance_pipeline.py       # End-to-end (collection → fetch → transcribe)
│   ├── test_acceptance_content_linking.py # Content deduplication and linking
│   └── test_acceptance_cli.py    # Alembic migration tests
├── alembic/                      # Database migrations (Alembic)
│   ├── versions/                 # Migration scripts
│   └── env.py, alembic.ini       # Alembic config
├── docs/                         # Documentation
│   ├── semantic-model.md         # Entity relationships, status lifecycles
│   ├── medallion-guidelines.md   # Bronze/Silver patterns, wrapper prescriptions
│   └── python-guidelines.md      # Module design, typing, import rules
├── data/                         # Runtime data (logs, temp files, models)
│   ├── logs/                     # Structured JSON logs (rotated)
│   ├── tmp/videos/               # Temporary yt-dlp downloads
│   └── models/                   # Cached ML models (whisper)
├── config.yaml                   # Main configuration (sources, settings overridden by env vars)
├── .env                          # Environment variables (database_url, API keys) [NOT committed]
├── .planning/codebase/           # GSD planning documents [Generated, NOT committed]
├── pyproject.toml                # Python project metadata, dependencies
├── Makefile                      # Dev commands (test, lint)
├── pytest.ini                    # pytest configuration
└── CLAUDE.md                     # Project instructions
```

## Directory Purposes

**`src/aggre/`:**
- Purpose: Main application package
- Contains: CLI, config, database models, collectors, pipeline modules, utilities, Dagster definitions
- Key files: `dagster_defs/__init__.py` (primary entry point), `db.py` (schema), `collectors/base.py` (plugin base)

**`src/aggre/utils/`:**
- Purpose: Generic reusable helpers with zero Aggre-specific logic
- Contains: Bronze filesystem writer, bronze-aware HTTP, shared HTTP client, structured logging
- Pattern: Implements medallion-guidelines.md patterns

**`src/aggre/collectors/`:**
- Purpose: Source-specific API clients
- Contains: One package per source type (hackernews, reddit, rss, youtube, lobsters, huggingface, telegram)
- Pattern: All inherit from BaseCollector, implement Collector protocol
- Each implements: `collect(engine, config, settings, log) -> int` (required), `search_by_url()` (optional for enrichment)

**`src/aggre/dagster_defs/`:**
- Purpose: Dagster orchestration layer
- Contains: Domain-aligned packages (collection, content, enrichment, transcription)
- Pattern: Each domain owns its job + sensor/schedule. Sensors use DatabaseResource parameter injection.
- Entry point: `dg.Definitions` composed in `__init__.py`

**`tests/`:**
- Purpose: Test suite for unit, integration, and acceptance tests
- Pattern: One test class per unit/feature, test methods named test_[scenario]
- Key fixture: `engine` (session-scoped PostgreSQL test database)

## Key File Locations

**Entry Points:**
- `src/aggre/dagster_defs/__init__.py`: Dagster definitions (primary orchestration)
- `src/aggre/cli.py`: Click CLI with `telegram-auth` command only
- `src/aggre/config.py`: Configuration loader (YAML + pydantic-settings with env var overrides)

**Database:**
- `src/aggre/db.py`: SQLAlchemy ORM models (Source, SilverDiscussion, SilverContent)
- `alembic/`: Database migrations (apply with `alembic upgrade head`)

**Core Logic:**
- `src/aggre/collectors/base.py`: BaseCollector with shared methods (_ensure_source, _upsert_discussion, etc.)
- `src/aggre/urls.py`: URL normalization and SilverContent deduplication (ensure_content)
- `src/aggre/content_downloader.py`: HTTP download → bronze (download_content)
- `src/aggre/content_extractor.py`: Bronze → silver text extraction (extract_html_text)
- `src/aggre/transcriber.py`: YouTube video transcription (transcribe)
- `src/aggre/enrichment.py`: Cross-source enrichment (enrich_content_discussions)

**Utilities:**
- `src/aggre/utils/http.py`: Shared HTTP client factory with User-Agent + proxy support
- `src/aggre/utils/logging.py`: Structured logging setup (structlog with JSON file + console output)
- `src/aggre/utils/bronze.py`: Bronze filesystem writer (write_bronze_json)
- `src/aggre/utils/bronze_http.py`: Bronze-aware HTTP wrapper (write_bronze_by_url)
- `src/aggre/statuses.py`: Status enums (FetchStatus, TranscriptionStatus, CommentsStatus)

## Where to Add New Code

**New Source Collector:**
1. Create `src/aggre/collectors/[source_type]/collector.py`
2. Inherit from BaseCollector, implement Collector protocol
3. `def collect(engine, config, settings, log) -> int:` (required)
4. `def search_by_url(url, engine, config, settings, log) -> int:` (optional, for enrichment)
5. Register in `src/aggre/collectors/__init__.py` COLLECTORS dict
6. Add Dagster ops to collection job or create new domain package in dagster_defs/
7. Add config model in `src/aggre/config.py`
8. Add tests in `tests/test_[source_type].py`

**New Content Pipeline Module:**
1. Create `src/aggre/[pipeline_stage].py`
2. Main function signature: `(engine, config, log, batch_limit=50) -> int | dict`
3. Query SilverContent by relevant status, process batches
4. Add Dagster job + sensor in `src/aggre/dagster_defs/[domain]/`
5. Create tests in `tests/test_[module].py`

**New Dagster Domain:**
1. Create `src/aggre/dagster_defs/[domain]/` with `__init__.py`, `job.py`, `sensor.py`
2. Sensor should accept `database: DatabaseResource` parameter
3. Register job, sensor in `src/aggre/dagster_defs/__init__.py`

---

*Structure analysis: 2026-02-22*
