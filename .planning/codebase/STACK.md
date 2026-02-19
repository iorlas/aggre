# Technology Stack

**Analysis Date:** 2026-02-20

## Languages

**Primary:**
- Python 3.12+ - All application code and CLI tools
- YAML - Configuration management (`config.yaml`)
- SQL - Database schema and Alembic migrations

**Secondary:**
- Bash - Docker entrypoints and deployment scripts

## Runtime

**Environment:**
- Python 3.14.2 (development)
- Python 3.12 (production target, as per `pyproject.toml`)

**Package Manager:**
- UV 0.5.13 - Fast Python package manager with lock file support
- Lockfile: `uv.lock` (present, frozen for reproducible builds)

## Frameworks

**Core:**
- Pydantic 2.12.2+ - Data validation and settings management via `pydantic-settings`
- SQLAlchemy 2.0+ - ORM and database abstraction (`src/aggre/db.py`)
- Alembic 1.15+ - Database migration tool (migrations in `alembic/` directory)

**CLI:**
- Click 8.1+ - Command-line interface framework (`src/aggre/cli.py`)

**Logging:**
- structlog 23.0.0+ - Structured logging with JSON and human-readable output (`src/aggre/logging.py`)

**Configuration:**
- PyYAML 6.0+ - YAML file parsing
- python-dotenv 1.2.1+ - Environment variable loading from `.env`

**Testing:**
- pytest 8.4.2+ - Test runner
- pytest-mock 3.15.1+ - Mocking fixtures
- pytest-recording 0.13.4+ - VCR-like HTTP response recording for contract tests

**Linting/Code Quality:**
- ruff 0.14.0+ - Fast Python linter (rules: E, F, I, N, W, UP; line length 140)
- ty 0.0.13 - Type checking tool for Python

## Key Dependencies

**Critical:**
- feedparser 6.0+ - RSS/Atom feed parsing (`src/aggre/collectors/rss.py`)
- yt-dlp 2024.0+ - YouTube metadata and video downloading (`src/aggre/collectors/youtube.py`)
- trafilatura 2.0+ - Article text extraction from HTML (`src/aggre/content_fetcher.py`)
- faster-whisper 1.0+ - OpenAI Whisper model for video transcription (`src/aggre/transcriber.py`)

**HTTP Client:**
- httpx 0.28+ with SOCKS proxy support - Modern async/sync HTTP client used by all collectors

**Retry & Resilience:**
- tenacity 9.1.2+ - Exponential backoff and retry logic for API calls

**Telegram:**
- telethon 1.37+ - Telegram MTProto client for public channel scraping (`src/aggre/collectors/telegram.py`)

**Database:**
- psycopg2-binary 2.9+ - PostgreSQL adapter for Python

## Configuration

**Environment:**
- Configuration via `.env` file (see `.env.example` for all options)
- Environment variables override YAML: `AGGRE_` prefix (e.g., `AGGRE_DATABASE_URL`)
- Settings loaded by `pydantic-settings` in `src/aggre/config.py`

**Key Environment Variables:**
- `AGGRE_DATABASE_URL` - PostgreSQL connection string (default: `postgresql+psycopg2://localhost/aggre`)
- `AGGRE_LOG_DIR` - Log output directory (default: `./data/logs`)
- `AGGRE_WHISPER_MODEL` - Whisper model size (default: `large-v3`)
- `AGGRE_WHISPER_MODEL_CACHE` - Model cache directory (default: `./data/models`)
- `AGGRE_YOUTUBE_TEMP_DIR` - Temporary video storage (default: `./data/tmp/videos`)
- `AGGRE_REDDIT_RATE_LIMIT` - Rate limit in seconds (default: 3.0)
- `AGGRE_HN_RATE_LIMIT` - Rate limit in seconds (default: 1.0)
- `AGGRE_LOBSTERS_RATE_LIMIT` - Rate limit in seconds (default: 2.0)
- `AGGRE_TELEGRAM_API_ID`, `AGGRE_TELEGRAM_API_HASH`, `AGGRE_TELEGRAM_SESSION` - Telegram credentials
- `AGGRE_TELEGRAM_RATE_LIMIT` - Rate limit in seconds (default: 2.0)
- `AGGRE_FETCH_LIMIT` - Max items per source per poll (default: 100)
- `AGGRE_PROXY_URL` - SOCKS5 proxy for HTTP and yt-dlp (e.g., `socks5://user:pass@geo.iproyal.com:32325`)

**Build:**
- `pyproject.toml` - Project metadata, dependencies, tool config
- `uv.lock` - Frozen dependency lock file
- `alembic.ini` - Database migration configuration
- `config.yaml` - Application configuration (sources, settings overrides)

## Platform Requirements

**Development:**
- Python 3.12+
- PostgreSQL 16+ (or compatible)
- ffmpeg (required by yt-dlp for video processing)
- build-essential tools (for native dependencies like ctranslate2)

**Production:**
- Docker with Docker Compose (see `docker-compose.yml` and `docker-compose.prod.yml`)
- PostgreSQL 16+ (containerized in compose setup)
- 10-20GB disk space for video cache (`./data/`)
- GPU optional (faster-whisper can use CUDA, but defaults to CPU)

## Containerization

**Base Image:**
- `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` - Lightweight UV-based Python 3.12 image

**Docker Services:**
- `postgres` - PostgreSQL 16 Alpine (stores all data)
- `migrate` - Runs Alembic migrations before other services
- `collect` - Collects discussions from configured sources (runs every 3600s)
- `download` - Downloads article/video content (runs every 10s)
- `extract-html-text` - Extracts text from downloaded HTML (runs every 10s)
- `enrich-content-discussions` - Discovers cross-source discussions (runs every 60s)
- `transcribe` - Transcribes YouTube videos (runs every 10s)
- `tor-proxy` - Optional Tor SOCKS proxy for anonymization

---

*Stack analysis: 2026-02-20*
