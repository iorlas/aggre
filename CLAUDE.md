# Aggre — Content Aggregation System

Aggre collects discussions from multiple sources (Hacker News, Reddit, Lobsters, RSS, YouTube, HuggingFace, Telegram), fetches linked content, and discovers cross-source discussions.

## Before Making Code Decisions

Read the relevant docs first:

- **Always read:** `docs/guidelines/semantic-model.md` — entity definitions, ubiquitous language, status lifecycles
- **Before changing Python code:** `docs/guidelines/python.md` — module design, typing, tooling, imports
- **Before changing code:** `.planning/codebase/CONVENTIONS.md` — code style, naming, imports, error handling
- **Before changing tests:** `.planning/codebase/TESTING.md` — fixtures, mocking patterns, factories
- **Before adding features:** `.planning/codebase/ARCHITECTURE.md` — layers, data flow, entry points
- **Before adding files:** `.planning/codebase/STRUCTURE.md` — directory layout, where to put new code
- **Before changing deps:** `.planning/codebase/STACK.md` — tech stack, versions, configuration
- **Before touching integrations:** `.planning/codebase/INTEGRATIONS.md` — external APIs, auth, rate limits
- **Before touching data layers:** `docs/guidelines/medallion.md` — medallion architecture, bronze/silver patterns
- **Before adding processing logic:** `docs/guidelines/component-contracts.md` — input accountability, disposition tracking
- **Before changing concurrency/pipeline:** `docs/guidelines/formal-verification.md` — TLA+ specs, spec-first workflow
- **Before refactoring:** `.planning/codebase/CONCERNS.md` — known tech debt, fragile areas

> **Note:** `.planning/codebase/` files are AI-generated snapshots of current codebase state, not human-authored guidelines. `docs/guidelines/` contains the human-authored standards.

## Dev Commands

- Run tests: `make test` or `uv run pytest tests/` (requires PostgreSQL — see `AGGRE_TEST_DATABASE_URL`)
- Run migrations: `alembic upgrade head`
- Lint: `make lint` (runs ruff check, ruff format --check, ty check)
- Dagster dev: `uv run dagster dev` (starts Dagster UI at http://localhost:3000)
- Dagster validate: `uv run dagster definitions validate`
- Verify TLA+ specs: `make verify` (requires Java)
