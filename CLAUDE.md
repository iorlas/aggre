# Aggre — Content Aggregation System

Aggre collects discussions from multiple sources (Hacker News, Reddit, Lobsters, RSS, YouTube, HuggingFace, Telegram), fetches linked content, and discovers cross-source discussions.

## Before Making Code Decisions

Read the relevant docs first:

- **When writing SQL or touching the data model:** `docs/reference/semantic-model.md` — schema, column semantics, query recipes, column ownership rules
- **When writing or modifying workflows:** `docs/reference/hatchet-operations.md` — SDK patterns, retry/timeout gotchas, zombie task mitigation
- **When adding a new data processing stage:** `docs/guidelines/medallion.md` — bronze/silver layer contracts, null-check pattern
- **When writing Python modules:** `docs/guidelines/python.md` — module design, typing, async patterns, layering
- **When writing tests:** `docs/guidelines/testing.md` — coverage thresholds, pragma conventions, test layers, mocking patterns
- **When deploying:** `/Users/iorlas/Documents/Knowledge/Researches/036-deployment-platform/guidelines/` — Dokploy, Traefik, CI/CD

## Dev Commands

- Full quality gate: `make check` — runs lint then test. Use before committing.
- Run tests: `make test` or `uv run pytest tests/` (requires PostgreSQL — see `AGGRE_TEST_DATABASE_URL`). Coverage is always reported — check for uncovered lines in files you changed.
- Check diff coverage: `make coverage-diff` — shows coverage of changed lines vs main. Fails below 95%. Run after writing tests to verify new code is covered.
- Run migrations: `alembic upgrade head`
- Lint: `make lint` (check only, never modifies files — safe for AI to run anytime)
- Fix: `make fix` (auto-fix formatting and import sorting, then runs `make lint` to verify)
- Audit: `make audit` (check for known vulnerabilities and leaked secrets — run after adding/updating deps)
- Hatchet worker: `uv run python -m aggre.workflows` (or `make worker`)
- Hatchet UI: http://localhost:8888 (via docker-compose)
- Verify TLA+ specs: `make verify` (requires Java)
- Pre-commit hooks run `make fix` then `make lint` automatically on every commit.
- Never truncate lint or test output — full output is needed for debugging.

## Hatchet Safety

- Never delete Hatchet volumes or modify Hatchet's internal database tables
- Use Hatchet's REST API for run management (cancel, replay, cleanup)
- Admin credentials are hardcoded in docker-compose — volume wipes auto-recover

## Column Ownership (Concurrency Safety)

Each processing stage writes only to columns it owns. No shared mutable columns between concurrent workflows. This guarantees safe concurrent updates without row locking.

| Stage | Columns Written | Table |
|-------|----------------|-------|
| Webpage (download+extract) | `text`, `title` | `silver_content` |
| Transcription | `text`, `detected_language`, `transcribed_by` | `silver_content` |
| Comments | `comments_json`, `comments_fetched_at` | `silver_discussions` |

Webpage and Transcription both write `text` but never process the same item (CEL filters partition by domain). When adding a new stage, give it its own columns or its own table — never share a mutable column with another stage.
