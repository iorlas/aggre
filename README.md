# Aggre

Content aggregation system. Collects discussions from Hacker News, Reddit, Lobsters, RSS, YouTube, HuggingFace, Telegram, and more. Fetches linked content, discovers cross-source discussions via URL matching, and processes content for analysis.

## Quick Start

```bash
make bootstrap    # Install deps, set up git hooks
cp .env.example .env  # Configure database URL and API keys
make dev          # Start local dev environment (Docker)
```

## Dev Commands

```bash
make lint         # Check only — never modifies files (safe for AI)
make fix          # Auto-fix formatting and import sorting
make test-e2e     # Run full test suite (spins up ephemeral postgres)
make worker       # Start Hatchet worker
make dev          # Local dev with Docker Compose
```

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Docker (for local dev and tests)
- [prek](https://github.com/j178/prek) (recommended) or pre-commit for git hooks: `brew install prek`
