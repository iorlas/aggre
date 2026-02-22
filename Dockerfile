FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# ffmpeg needed by yt-dlp, build-essential for native deps (ctranslate2 etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg build-essential \
    && rm -rf /var/lib/apt/lists/*

# Deps first (cache layer)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Source + install project
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY dagster.yaml workspace.yaml ./
RUN uv sync --frozen --no-dev

# Skip sync at runtime
ENV UV_NO_SYNC=true
