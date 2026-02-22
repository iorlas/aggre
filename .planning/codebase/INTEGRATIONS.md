# External Integrations

**Analysis Date:** 2026-02-20

## APIs & External Services

**Discussion Sources:**
- Reddit JSON API - Subreddit discussions and comments
  - SDK/Client: httpx (HTTP client via `src/aggre/http.py`)
  - Auth: No authentication required (public endpoints)
  - Rate limiting: `AGGRE_REDDIT_RATE_LIMIT` env var (default 3.0s)
  - Implementation: `src/aggre/collectors/reddit.py` - Fetches `/r/{subreddit}/.json` with adaptive rate limiting based on response headers

- Hacker News Algolia API - HN stories and comments
  - SDK/Client: httpx
  - Auth: No authentication required
  - Rate limiting: `AGGRE_HN_RATE_LIMIT` env var (default 1.0s)
  - Implementation: `src/aggre/collectors/hackernews.py` - Queries `https://hn.algolia.com/api/v1` endpoints
  - API: Unofficial but stable Algolia-hosted HN search

- Lobsters REST API - Lobsters stories
  - SDK/Client: httpx
  - Auth: No authentication required
  - Rate limiting: `AGGRE_LOBSTERS_RATE_LIMIT` env var (default 2.0s)
  - Implementation: `src/aggre/collectors/lobsters.py`

- YouTube - Video metadata and transcription
  - SDK/Client: yt-dlp (via `src/aggre/collectors/youtube.py`)
  - Auth: No authentication required (public channels only)
  - Transcription: Video audio extracted and transcribed via faster-whisper
  - Proxy support: Routed through `AGGRE_PROXY_URL` if configured

- HuggingFace Daily Papers - Research paper listings
  - SDK/Client: httpx
  - Auth: No authentication required
  - API endpoint: `https://huggingface.co/api/daily_papers` (undocumented JSON API)
  - Implementation: `src/aggre/collectors/huggingface.py`

- Telegram - Public channel messages
  - SDK/Client: Telethon (MTProto client)
  - Auth: User credentials required
    - `AGGRE_TELEGRAM_API_ID` - API ID from https://my.telegram.org
    - `AGGRE_TELEGRAM_API_HASH` - API hash from https://my.telegram.org
    - `AGGRE_TELEGRAM_SESSION` - Base64-encoded StringSession (generate via `aggre telegram-auth`)
  - Rate limiting: `AGGRE_TELEGRAM_RATE_LIMIT` env var (default 2.0s)
  - Implementation: `src/aggre/collectors/telegram.py` - Async collector using Telethon client

- RSS/Atom Feeds - Generic feed ingestion
  - SDK/Client: feedparser
  - Auth: Varies by feed (basic auth supported via URL)
  - Implementation: `src/aggre/collectors/rss.py`

## Data Storage

**Primary Database:**
- PostgreSQL 16+
  - Connection: `postgresql+psycopg2://[user]:[password]@[host]/[database]`
  - Configured via: `AGGRE_DATABASE_URL` env var (default: `postgresql+psycopg2://localhost/aggre`)
  - Client: psycopg2-binary (via SQLAlchemy ORM)
  - Tables: `sources`, `silver_discussions`, `silver_content` (see `src/aggre/db.py`)
  - Migrations: Alembic (run via `alembic upgrade head`, automated in Docker)

**File Storage:**
- Local filesystem only (no S3/cloud storage)
  - Video cache: `AGGRE_YOUTUBE_TEMP_DIR` (default `./data/tmp/videos/`)
  - Whisper models: `AGGRE_WHISPER_MODEL_CACHE` (default `./data/models/`)
  - Application data: `./data/app/` (mounted in Docker)
  - PostgreSQL data: `./data/postgres/` (mounted in Docker)

**Caching:**
- None (all data persisted to PostgreSQL)

## Authentication & Identity

**Auth Provider:**
- Custom/None - System does not handle user authentication
- Telegram: User-based authentication via MTProto protocol (not OAuth)
- All other sources: Public API access (no authentication required)

## Monitoring & Observability

**Error Tracking:**
- None (no integration with Sentry, Rollbar, etc.)

**Logs:**
- Structured logging via structlog
- Output: Dual streams configured in `src/aggre/logging.py`
  - **Stdout**: Human-readable console output (INFO level)
  - **File**: JSON Lines format (`./data/logs/aggre.log`, rotated at 10MB, keeps 5 backups)
- Log level: DEBUG for file, INFO for console
- All log events include timestamps (ISO format) and structured fields

## CI/CD & Deployment

**Hosting:**
- Docker/Docker Compose (no cloud platform integration)
- `docker-compose.yml` - Development setup with Tor proxy
- `docker-compose.prod.yml` - Production setup

**CI Pipeline:**
- None (no GitHub Actions, GitLab CI, etc. configured)
- Pre-commit hooks: Configured in `.pre-commit-config.yaml` for local development

**Deployment:**
- Docker image: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` (multi-stage build)
- Services: Orchestrated via Docker Compose (see `docker-compose.yml` for service definitions)
- Database initialization: Automatic via `migrate` service running Alembic before other services start

## Environment Configuration

**Required Environment Variables:**
- `AGGRE_DATABASE_URL` - PostgreSQL connection string (critical)
- `AGGRE_TELEGRAM_API_ID` - Telegram API ID (only if collecting from Telegram)
- `AGGRE_TELEGRAM_API_HASH` - Telegram API hash (only if collecting from Telegram)
- `AGGRE_TELEGRAM_SESSION` - Telegram session string (only if collecting from Telegram)

**Optional Configuration:**
- `AGGRE_PROXY_URL` - SOCKS5 proxy for HTTP requests and yt-dlp (format: `socks5://user:pass@host:port`)
- All rate limit settings: `AGGRE_*_RATE_LIMIT` (each has sensible defaults)
- Paths: `AGGRE_LOG_DIR`, `AGGRE_YOUTUBE_TEMP_DIR`, `AGGRE_WHISPER_MODEL_CACHE`
- Whisper model: `AGGRE_WHISPER_MODEL` (default `large-v3`)

**Secrets Location:**
- `.env` file (not committed; see `.env.example` for template)
- Docker: `env_file` directive in compose files points to `.env`
- No secrets in code or config.yaml

## Webhooks & Callbacks

**Incoming:**
- None (polling-based collection only)

**Outgoing:**
- None (no notifications to external systems)

## Network & Proxy

**HTTP Client:**
- All HTTP requests (Reddit, HN, HuggingFace, trafilatura downloads) use `httpx.Client` with shared configuration
- Proxy support: All clients support `AGGRE_PROXY_URL` (SOCKS5 format)
- User-Agent: Browser-like User-Agent header (`Mozilla/5.0...`) to avoid blocking
- Timeout: 30 seconds default (configurable per request)
- Implementation: `src/aggre/http.py` factory function `create_http_client()`

**yt-dlp:**
- Routed through SOCKS5 proxy if `AGGRE_PROXY_URL` configured
- Used for YouTube metadata extraction and video download
- ffmpeg required for video processing (installed in Docker)

## Data Processing Pipeline

**Content Fetcher:**
- Downloads HTML from URLs discovered in discussions
- Uses trafilatura for text extraction
- Skips YouTube, PDF, and other non-HTML content
- Updates `SilverContent` records with fetch status and body text
- Implementation: `src/aggre/content_fetcher.py`

**Transcriber:**
- Processes YouTube videos via yt-dlp download + faster-whisper transcription
- Stores transcripts in `SilverContent.body_text`
- Tracks transcription status (PENDING, DOWNLOADING, TRANSCRIBING, COMPLETED, FAILED)
- Implementation: `src/aggre/transcriber.py`

**Enrichment:**
- Discovers cross-source discussions for known content URLs
- Queries each source API for discussions about a URL (SearchableCollector pattern)
- Updates `SilverDiscussion` records with content_id foreign key
- Implementation: `src/aggre/enrichment.py`

---

*Integration audit: 2026-02-20*
