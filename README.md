# Aggre

Content aggregation system. Polls RSS feeds, Reddit, and YouTube channels, storing content in a local SQLite database.

## Setup

```bash
uv sync
uv run alembic upgrade head
```

## Usage

```bash
aggre fetch                          # Poll all sources once
aggre fetch --source reddit          # Poll only Reddit
aggre fetch --loop --interval 3600   # Poll every hour
aggre transcribe                     # Transcribe pending YouTube videos
aggre status                         # Show fetch times, queue sizes
```

## Docker

```bash
docker-compose up -d
```
