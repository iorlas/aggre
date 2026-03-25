FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# ffmpeg: yt-dlp audio extraction; curl+unzip: deno install
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip \
    && DENO_ARCH=$(if [ "$TARGETARCH" = "arm64" ]; then echo "aarch64"; else echo "x86_64"; fi) \
    && curl -fsSL "https://dl.deno.land/release/v2.3.5/deno-${DENO_ARCH}-unknown-linux-gnu.zip" -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && apt-get purge -y curl unzip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Deps first (cache layer)
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Source + install project
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY config.yaml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Non-root user
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app
USER app

# Skip sync at runtime
ENV UV_NO_SYNC=true

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import sys; sys.exit(0)"]
