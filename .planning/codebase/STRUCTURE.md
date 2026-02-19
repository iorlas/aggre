# Codebase Structure

**Analysis Date:** 2026-02-20

## Directory Layout

```
/Users/iorlas/Projects/my/aggre/
├── src/aggre/                 # Main package source
│   ├── __init__.py            # Package version
│   ├── cli.py                 # Click CLI commands (collect, download, transcribe, etc.)
│   ├── config.py              # YAML config loading + pydantic Settings
│   ├── db.py                  # SQLAlchemy ORM models (Source, BronzeDiscussion, SilverDiscussion, SilverContent)
│   ├── statuses.py            # Status enums (FetchStatus, TranscriptionStatus, CommentsStatus)
│   ├── urls.py                # URL normalization and SilverContent management
│   ├── http.py                # Shared HTTP client factory (httpx)
│   ├── logging.py             # Structured logging setup (structlog + stdlib)
│   ├── worker.py              # Reusable loop and CLI decorator helpers
│   ├── content_fetcher.py      # Article download and HTML text extraction (trafilatura)
│   ├── transcriber.py         # YouTube video transcription (yt-dlp + faster-whisper)
│   ├── enrichment.py          # Cross-source enrichment (search HN/Lobsters for URLs)
│   └── collectors/            # Source-specific collector plugins
│       ├── base.py            # BaseCollector shared helpers
│       ├── hackernews.py      # Hacker News collector (Algolia API)
│       ├── reddit.py          # Reddit collector (PRAW)
│       ├── rss.py             # RSS/Atom collector (feedparser)
│       ├── youtube.py         # YouTube collector (yt-dlp)
│       ├── lobsters.py        # Lobsters collector (HTTP API)
│       ├── huggingface.py      # HuggingFace Papers collector (HTTP API)
│       └── telegram.py        # Telegram collector (Telethon)
├── tests/                     # Test suite
│   ├── conftest.py            # pytest fixtures (PostgreSQL test engine, table cleanup)
│   ├── test_urls.py           # Tests for URL normalization and ensure_content()
│   ├── test_content.py        # Tests for content fetcher state transitions
│   ├── test_enrichment.py     # Tests for enrichment pipeline
│   ├── test_hackernews.py     # Tests for HackerNews collector
│   ├── test_reddit.py         # Tests for Reddit collector
│   ├── test_rss.py            # Tests for RSS collector
│   ├── test_youtube.py        # Tests for YouTube collector
│   ├── test_lobsters.py       # Tests for Lobsters collector
│   ├── test_huggingface.py     # Tests for HuggingFace collector
│   ├── test_telegram.py       # Tests for Telegram collector
│   ├── test_acceptance_pipeline.py      # End-to-end tests (collection → fetch → transcribe)
│   ├── test_acceptance_content_linking.py # Tests for content deduplication and linking
│   └── test_acceptance_cli.py  # Tests for CLI commands
├── alembic/                   # Database migrations (Alembic)
│   ├── versions/              # Migration scripts
│   └── env.py, alembic.ini    # Alembic config
├── docs/                      # Documentation
│   └── semantic-model.md      # Entity relationships, status lifecycles, process mappings
├── data/                      # Runtime data (logs, temp files, models)
│   ├── logs/                  # Structured JSON logs (rotated)
│   ├── tmp/                   # Temporary files (downloaded videos)
│   │   └── videos/
│   └── models/                # Cached ML models (whisper, etc.)
├── config.yaml                # Main configuration (sources, settings overridden by env vars)
├── .env                       # Environment variables (database_url, API keys) [NOT committed]
├── .planning/                 # GSD planning documents [Generated, NOT committed]
│   └── codebase/              # Codebase analysis docs
│       ├── ARCHITECTURE.md    # Pattern, layers, data flow
│       ├── STRUCTURE.md       # This file
│       ├── CONVENTIONS.md     # Naming, style, import patterns [if generated]
│       ├── TESTING.md         # Test framework, patterns [if generated]
│       ├── STACK.md           # Tech stack, versions [if generated]
│       ├── INTEGRATIONS.md    # External APIs, services [if generated]
│       └── CONCERNS.md        # Technical debt, issues [if generated]
├── pyproject.toml             # Python project metadata, dependencies
├── pytest.ini                 # pytest configuration
├── .gitignore                 # Git ignore rules
├── README.md                  # Project overview
└── CLAUDE.md                  # Project instructions (ubiquitous language, architecture pointers)
```

## Directory Purposes

**`src/aggre/`:**
- Purpose: Main application package
- Contains: CLI, config, database models, collectors, pipeline modules, utilities
- Key files: `cli.py` (entry point), `db.py` (schema), `collectors/base.py` (plugin base)

**`src/aggre/collectors/`:**
- Purpose: Source-specific API clients
- Contains: One file per source type (hackernews, reddit, rss, youtube, lobsters, huggingface, telegram)
- Pattern: All inherit from BaseCollector, implement Collector protocol
- Each implements: `collect(engine, config, log) -> int` (required), `search_by_url()` (optional for enrichment)

**`tests/`:**
- Purpose: Test suite for unit, integration, and acceptance tests
- Contains: Test files colocated with test name (test_[module].py), acceptance tests, fixtures
- Pattern: One test class per unit/feature, test methods named test_[scenario]
- Key fixture: `engine` (session-scoped PostgreSQL test database)

**`alembic/`:**
- Purpose: Database schema versioning
- Contains: Migration scripts in `versions/` directory
- Run: `alembic upgrade head` during development setup

**`docs/`:**
- Purpose: Project documentation
- Contains: Semantic model, architecture decisions
- Key file: `semantic-model.md` (entity definitions, relationships, status lifecycles)

**`data/`:**
- Purpose: Runtime artifacts
- Contains: Logs (`data/logs/`), temporary files (`data/tmp/videos/`), ML models (`data/models/`)
- Generated: Yes, created by CLI commands
- Committed: No (in .gitignore)

**`.planning/codebase/`:**
- Purpose: GSD planning documents (generated by /gsd:map-codebase)
- Contains: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, STACK.md, INTEGRATIONS.md, CONCERNS.md
- Generated: Yes (by GSD agent)
- Committed: No (in .gitignore, for local planning only)

## Key File Locations

**Entry Points:**
- `src/aggre/cli.py`: Main Click CLI with commands: collect, download, extract-html-text, enrich-content-discussions, transcribe, backfill, backfill-content, status, telegram-auth
- `src/aggre/config.py`: Configuration loader (YAML + pydantic-settings with env var overrides)

**Database:**
- `src/aggre/db.py`: SQLAlchemy ORM models (Source, BronzeDiscussion, SilverDiscussion, SilverContent)
- `alembic/`: Database migrations (apply with `alembic upgrade head`)

**Core Logic:**
- `src/aggre/collectors/base.py`: BaseCollector with shared methods (_ensure_source, _store_raw_item, _upsert_discussion, etc.)
- `src/aggre/urls.py`: URL normalization and SilverContent deduplication (ensure_content)
- `src/aggre/content_fetcher.py`: Article download and text extraction (download_content, extract_html_text)
- `src/aggre/transcriber.py`: YouTube video transcription (transcribe with yt-dlp + faster-whisper)
- `src/aggre/enrichment.py`: Cross-source enrichment (search HN/Lobsters for URLs)

**Utilities:**
- `src/aggre/http.py`: Shared HTTP client factory with User-Agent + proxy support
- `src/aggre/logging.py`: Structured logging setup (structlog with JSON file + console output)
- `src/aggre/worker.py`: Reusable loop and CLI decorator helpers (worker_options, run_loop)
- `src/aggre/statuses.py`: Status enums (FetchStatus, TranscriptionStatus, CommentsStatus)

**Collectors:**
- `src/aggre/collectors/hackernews.py`: HackerNews (Algolia API, searchable)
- `src/aggre/collectors/reddit.py`: Reddit (PRAW, includes comment fetching)
- `src/aggre/collectors/rss.py`: RSS/Atom (feedparser)
- `src/aggre/collectors/youtube.py`: YouTube (yt-dlp)
- `src/aggre/collectors/lobsters.py`: Lobsters (HTTP API, searchable)
- `src/aggre/collectors/huggingface.py`: HuggingFace Papers (HTTP API)
- `src/aggre/collectors/telegram.py`: Telegram (Telethon async)

**Testing:**
- `tests/conftest.py`: Shared fixtures (PostgreSQL engine, table cleanup)
- `tests/test_urls.py`: URL normalization and ensure_content tests
- `tests/test_content.py`: Content fetcher state transition tests
- `tests/test_enrichment.py`: Enrichment pipeline tests
- `tests/test_hackernews.py`: HackerNews collector tests
- `tests/test_reddit.py`: Reddit collector tests
- `tests/test_rss.py`: RSS collector tests
- `tests/test_youtube.py`: YouTube collector tests
- `tests/test_acceptance_pipeline.py`: End-to-end collection → fetch → transcribe
- `tests/test_acceptance_content_linking.py`: Content deduplication and linking
- `tests/test_acceptance_cli.py`: CLI command integration tests

## Naming Conventions

**Files:**
- Modules: lowercase with underscores (`content_fetcher.py`, `base.py`)
- Tests: `test_[module].py` for unit tests, `test_acceptance_[feature].py` for acceptance tests
- Collectors: `[source_type].py` (e.g., `hackernews.py`, `reddit.py`)

**Directories:**
- Package: lowercase single word (`aggre`)
- Sub-package: lowercase plural (`collectors`)
- Tests: `tests` (no package structure, all fixtures in conftest.py)
- Data: `data/` with subdirectories `logs/`, `tmp/`, `models/`

**Classes:**
- Models: PascalCase (Source, BronzeDiscussion, SilverDiscussion, SilverContent)
- Collectors: PascalCase ending in "Collector" (HackernewsCollector, RedditCollector)
- Base classes: PascalCase (BaseCollector)
- Enums: PascalCase (FetchStatus, TranscriptionStatus, CommentsStatus)

**Functions:**
- Public: snake_case (normalize_url, ensure_content, download_content)
- Internal: _leading_underscore (\_store_raw_item, \_update_last_fetched)
- State transitions: verb_noun pattern (content_downloaded, transcription_completed)
- Helpers in collectors: \_leading_underscore (_store_discussion, _fetch_comments)

**Variables/Database:**
- Database fields: snake_case (external_id, source_type, published_at)
- External IDs: external_id (not post_id, story_id, video_id internally after parsing)
- Discussion IDs: discussion_id (not ci_id or source-specific naming)
- Configuration: snake_case (fetch_limit, reddit_rate_limit)

## Where to Add New Code

**New Source Collector:**
1. Create `src/aggre/collectors/[source_type].py`
2. Inherit from BaseCollector, implement Collector protocol
3. `def collect(engine, config, log) -> int:` (required)
4. `def search_by_url(url, engine, config, log) -> int:` (optional, for enrichment)
5. Use helpers: `_ensure_source()`, `_store_raw_item()`, `_upsert_discussion()`, `_update_last_fetched()`
6. Import in `src/aggre/cli.py` and add to collectors dict in collect_cmd()
7. Add config model in `src/aggre/config.py` (e.g., RedditSource, YoutubeSource)
8. Add tests in `tests/test_[source_type].py` with fixtures, mocking APIs

**New Content Pipeline Module:**
1. Create `src/aggre/[pipeline_stage].py`
2. Main function signature: `(engine, config, log, batch_limit=50) -> int | dict`
3. Query SilverContent by relevant status, process batches
4. Use state transition functions for semantic clarity
5. Log with dot-notation (e.g., `module.step_complete`)
6. Add CLI command in `src/aggre/cli.py` with @worker_options decorator
7. Create tests in `tests/test_[module].py`

**New Database Entity:**
1. Add ORM model to `src/aggre/db.py`
2. Define indexes (sa.Index definitions at bottom of db.py)
3. Create migration: `alembic revision --autogenerate -m "description"`
4. Update `docs/semantic-model.md` with entity definition
5. Update tests if affected by schema change

**New Utility Function:**
- Shared URL logic: `src/aggre/urls.py`
- Shared HTTP logic: `src/aggre/http.py`
- Shared CLI logic: `src/aggre/worker.py`
- Status enums: `src/aggre/statuses.py`
- Logging: `src/aggre/logging.py`

**New Test:**
- Unit tests: `tests/test_[module].py` as test class with test_* methods
- Acceptance tests: `tests/test_acceptance_[feature].py`
- Fixtures: Add to `tests/conftest.py` if shared across tests, otherwise define in test file

## Special Directories

**`alembic/versions/`:**
- Purpose: Database migration scripts (automatically generated)
- Generated: Yes (via `alembic revision --autogenerate`)
- Committed: Yes (track schema changes)
- Do not edit manually: Let alembic generate them

**`data/logs/`:**
- Purpose: Structured JSON logs (rotating file handler)
- Generated: Yes (by CLI commands)
- Committed: No (.gitignore)
- Retention: 5 backup files, 10 MB per file (configurable in logging.py)

**`data/tmp/videos/`:**
- Purpose: Temporary directory for yt-dlp video downloads during transcription
- Generated: Yes (by transcriber.py)
- Committed: No (.gitignore)
- Cleanup: Manual (files persist after transcription for re-processing)

**`data/models/`:**
- Purpose: Cached ML models (faster-whisper downloads)
- Generated: Yes (first transcription command downloads whisper model)
- Committed: No (.gitignore)
- Location: Configured via AGGRE_WHISPER_MODEL_CACHE env var

---

*Structure analysis: 2026-02-20*
