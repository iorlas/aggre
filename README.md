# Aggre

**Content aggregation system.** Collects discussions from Hacker News, Reddit, Lobsters, RSS, YouTube, HuggingFace, Telegram, and more. Fetches linked content, discovers cross-source discussions via URL matching, and processes content for analysis.

## Quick Start

```bash
make bootstrap        # Install deps + git hooks
cp .env.example .env  # Configure API keys (optional for local dev)
make dev              # Start full local environment (Docker Compose)
```

Hatchet UI at http://localhost:8888 (login: `admin@example.com` / `Admin123!!`)
Grafana at http://localhost:3002 (anonymous access)

## Dev Commands

```bash
make dev              # Local dev with hot-reload (Docker Compose watch)
make test             # Run tests (spins up ephemeral postgres automatically)
make check            # Full quality gate: lint + test
make lint             # Lint only — safe for AI, never modifies files
make fix              # Auto-fix formatting + imports, then lint
make audit            # Check for vulnerabilities and leaked secrets
make coverage-diff    # Coverage of changed lines vs main (95% threshold)
make worker           # Start Hatchet worker (outside Docker)
make verify           # Verify TLA+ formal specs (requires Java)
```

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Docker (for local dev and tests)
- [prek](https://github.com/j178/prek) (recommended) or pre-commit for git hooks

## Project Structure

```
src/aggre/            # Application code
tests/                # Test suite
alembic/              # Database migrations
docs/                 # Guidelines, decisions, specs
  guidelines/         # Python, testing, medallion conventions
  decisions/          # Architecture decision records
  reference/          # Operational guides (Hatchet, semantic model)
grafana/              # Dashboards and provisioning
verification/         # TLA+ formal specifications
scripts/              # Operational and automation scripts
```

### Docker Compose Files

| File | Purpose | Used by |
|------|---------|---------|
| `docker-compose.local.yml` | Full local dev environment | `make dev` |
| `docker-compose.prod.yml` | Production stack (Dokploy) | CI/CD |
| `docker-compose.test.yml` | Ephemeral test database | `make test` |

## Architecture

- **Orchestration**: [Hatchet](https://hatchet.run/) — event-driven per-item workflows with concurrency control
- **Database**: PostgreSQL 17 — medallion architecture (bronze/silver layers)
- **Object Storage**: [Garage](https://garagehq.deuxfleurs.fr/) — S3-compatible, stores bronze layer media
- **Deployment**: Dokploy on self-hosted VPS, Traefik ingress, Tailscale mesh networking
- **CI/CD**: GitHub Actions — lint, test, build multi-arch Docker image, deploy
