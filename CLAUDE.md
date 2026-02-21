# Aggre — Content Aggregation System

Aggre collects discussions from multiple sources (Hacker News, Reddit, Lobsters, RSS, YouTube, HuggingFace, Telegram), fetches linked content, and discovers cross-source discussions.

## Before Writing Code

Read the relevant docs first:

- **Always read:** `docs/semantic-model.md` — entity definitions, ubiquitous language, status lifecycles
- **Before writing code:** `.planning/codebase/CONVENTIONS.md` — code style, naming, imports, error handling
- **Before writing tests:** `.planning/codebase/TESTING.md` — fixtures, mocking patterns, factories
- **Before adding features:** `.planning/codebase/ARCHITECTURE.md` — layers, data flow, entry points
- **Before adding files:** `.planning/codebase/STRUCTURE.md` — directory layout, where to put new code
- **Before changing deps:** `.planning/codebase/STACK.md` — tech stack, versions, configuration
- **Before touching integrations:** `.planning/codebase/INTEGRATIONS.md` — external APIs, auth, rate limits
- **Before touching data layers:** `docs/medallion-guidelines.md` — medallion architecture, bronze/silver patterns
- **Before refactoring:** `.planning/codebase/CONCERNS.md` — known tech debt, fragile areas

## Dev Commands

- Run tests: `pytest tests/` (requires PostgreSQL — see `AGGRE_TEST_DATABASE_URL`)
- Run migrations: `alembic upgrade head`
- Lint: `ruff check src/`
