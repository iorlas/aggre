# Python Guidelines

Opinionated guidelines for Python code structure, typing, and module design. Consumed by coding agents. Prescribes what to do and why — not a style guide describing options.

Scope: Python code conventions and module design. Does NOT cover data layer architecture (`docs/medallion-guidelines.md`) or domain model semantics (`docs/semantic-model.md`).

## Project Layout

### src Layout

Use `src/{package}/` layout. Source code lives under `src/aggre/`, not at repo root.

Why: prevents accidental imports of uninstalled package during development. `import aggre` only works after `pip install -e .`, which ensures dependencies are resolved.

Why not flat layout: flat layout (`aggre/` at repo root) allows importing without installing, masking missing dependencies. Caught in CI but not locally.

### Package Decomposition

One application package (`aggre`). Sub-packages by bounded context (`collectors/`).

Current structure: `aggre/` root contains shared infrastructure + small pipeline modules. `collectors/` contains per-source integrations as sub-packages.

Add new sub-packages only when: (a) a group of related files exceeds 5+ files, (b) the group has clear internal coupling and loose external coupling, and (c) the group maps to a business boundary an agent would own entirely.

Why not separate top-level packages (`aggre_hackernews`): shared code (BaseCollector, db models) becomes cross-package dependency. Testing harder. Deployment requires multiple installs. Plugin ecosystem pattern — wrong for single-repo applications.

Why not split by technical layer (`core/`, `pipeline/`): current flat structure works because files are small (<200 lines each) and names are self-explanatory. Layer-based splitting adds directory nesting without reducing merge conflicts — agents already work on distinct files.

### Dependency Layers

Imports form a DAG with three layers:

```
Layer 3 (composition root): cli.py, config.py
  ↓ imports from
Layer 2 (business modules): collectors/*, content_fetcher.py, transcriber.py, enrichment.py
  ↓ imports from
Layer 1 (infrastructure): db.py, statuses.py, urls.py, http.py, logging.py, settings.py, worker.py
```

Layer 1 modules never import from layer 2 or 3. Layer 2 never imports from layer 3.

Layer 2 modules never import from each other. Each business module is independent.

`collectors/__init__.py` (registry) is layer 3 — it's a composition concern.

Why: layer violations create coupling that spreads merge conflicts. Independent layer-2 modules mean agents work without interference.

## Module Design

### Business-First Boundaries

Each module owns one business capability (e.g., `collectors/hackernews/` owns HN integration, `content_fetcher.py` owns article downloading).

Boundary test: "would two AI agents ever touch this same file simultaneously?" If yes, the module is doing too much — split along business lines.

Shared utilities (`http.py`, `urls.py`, `statuses.py`) are stable infrastructure — they change rarely and are read-only dependencies for business modules.

Why: merge conflicts from concurrent AI agents are the biggest velocity killer. Business-aligned modules mean agents work in isolated directories.

### File vs Package

- **Single file**: module under ~200 lines with one public concern. E.g., `enrichment.py`, `urls.py`, `http.py`.
- **Package**: module with multiple internal files or sub-concerns. E.g., `collectors/hackernews/` (collector.py + config.py).

Default to single file. Promote to package when: (a) separate config class needed, (b) multiple internal files, or (c) file exceeds ~300 lines.

Package internal structure: `collector.py` (implementation), `config.py` (pydantic models), `__init__.py` (only `__all__` declaration, no logic).

### No Barrel Files

`__init__.py` files contain at most `__all__ = [...]` for package identity. No re-exports, no logic, no imports of submodules.

Always import from the actual module: `from aggre.collectors.hackernews.collector import HackernewsCollector`, never `from aggre.collectors.hackernews import HackernewsCollector`.

Exception: registry patterns (e.g., `collectors/__init__.py` building a `COLLECTORS` dict) — allowed because the registry is the module's purpose, not a convenience re-export.

Why: barrel files create implicit coupling, hide import chains, cause circular dependencies, and make it hard to trace where code lives. Direct imports are grep-friendly.

### Circular Dependency Prevention

Rule: imports form a DAG. If module A imports from B, B must never import from A (directly or transitively).

Pattern: shared types and protocols go in leaf modules (`statuses.py`, `db.py`). Business modules depend on leaves, never on each other.

If two modules need each other: extract the shared concept into a new leaf module.

CLI (`cli.py`) is the only module that imports from everywhere — it's the composition root.

Why: circular imports cause `ImportError` at runtime and signal that module boundaries are wrong.

## Configuration

### Two-Tier Settings

- **Tier 1 — Settings** (`BaseSettings`, env vars): operational and environment-specific params. Database URL, log paths, API keys/secrets, rate limits, model paths. Differs between dev/staging/prod.
- **Tier 2 — Collector configs** (`BaseModel`, YAML): business logic params. Which sources to collect, fetch limits, source-specific fields (subreddit names, channel IDs, RSS URLs). Same across environments.

Boundary rule: "does this value change per deployment environment?" → Settings (env vars). "Does this value define what the app does?" → collector config (YAML).

Why: secrets and infra never go in YAML (committed to repo). Business config shouldn't require env var juggling.

### Collector Config Pattern

Each collector sub-package defines its own `config.py` with two models:

- `{Collector}Source(BaseModel)`: per-source fields (name, URL, channel_id, subreddit). Represents one configured source.
- `{Collector}Config(BaseModel)`: operational fields (fetch_limit, init_fetch_limit) + `sources: list[{Collector}Source]`. Represents all configuration for one collector type.

Config models are pure data — no methods, no imports from collector implementation.

Why: config is a leaf dependency. Collector imports config, never the reverse. Clean DAG.

### Explicit Composition

Root `AppConfig` in `config.py` explicitly imports and composes all collector configs as typed fields.

Adding a new collector requires editing three files: (1) `config.py` — add import + field, (2) `collectors/__init__.py` — add to COLLECTORS dict, (3) `cli.py` — if new CLI commands needed.

This is the idiomatic Python pattern. Django uses `INSTALLED_APPS`, FastAPI uses `include_router()`, Scrapy uses `SPIDER_MODULES`. Even "auto-discovery" projects require explicit lists of where to look.

Why not auto-discovery (`pkgutil`, entry_points, decorators): loses static typing (no IDE autocompletion on `cfg.hackernews`), adds convention-based magic that silently fails, debugging overhead. 7 collectors don't justify the complexity.

Why not entry_points: designed for cross-package plugin ecosystems. Wrong for a single-repo where all modules are known at dev time.

## Type System

### No Any

`Any` is banned in new code. Existing `Any` usage is tech debt to be eliminated.

When you think you need `Any`: use `Protocol`, `TypeVar`, `Generic`, union types, or `object`.

For truly unknown external data (JSON from APIs): use `dict[str, object]` or define a TypedDict/Pydantic model.

For callback parameters: use `Callable[..., T]` with specific return type, or define a Protocol with `__call__`.

Why: `Any` disables type checking at the boundary. One `Any` propagates through call chains and silently defeats the type checker.

### Modern Syntax

`from __future__ import annotations` in every file (PEP 563).

Union: `X | None` not `Optional[X]`.

Built-in generics: `list[str]`, `dict[str, int]`, `tuple[int, ...]` — not `List`, `Dict`, `Tuple`.

Return type on every function, including `-> None`.

Why: consistent, modern, readable. Future annotations enable forward references without quotes.

### Protocol Over ABC

Prefer `Protocol` for interfaces. Use ABC only when you need shared implementation (like `BaseCollector`).

Protocol defines shape. ABC defines shared behavior. Don't mix.

Why: Protocols enable structural subtyping — no inheritance needed. Better for loose coupling and testability.

### Typed Configuration

All config classes use Pydantic `BaseModel` (YAML-loaded) or `BaseSettings` (env vars).

Collector configs typed per-source: `HackernewsConfig`, `RedditConfig` — not `Any` or `dict`.

Why: typed configs catch misconfigurations at load time, not at runtime deep in business logic.

## Constants and Enums

### StrEnum for All Enums

All enums use `StrEnum` with lowercase values matching DB strings directly (`FetchStatus.PENDING == "pending"` is `True`).

No plain string literals for values that have a fixed vocabulary — define a `StrEnum`.

Why `StrEnum` over `Enum`: string comparison works without `.value`. DB queries, assertions, and log messages all work without coercion.

### frozenset for Constant Sets

Membership-test sets (domain skip lists, tracking params) use `frozenset`. Never mutable `set`.

Why: signals immutability at the type level. Prevents accidental `.add()` / `.remove()`. Marginally faster for `in` tests.

### Module-Level Constants

`UPPER_SNAKE_CASE` for module-level constants (URLs, user agents, column tuples).

## Tooling

### Linter (ruff)

Rules: `["E", "F", "I", "N", "W", "UP"]`. Line length 140. Target Python 3.12.

Run: `ruff check src/`.

Must pass before commit.

### Type Checker (ty)

Configured in `pyproject.toml` under `[tool.ty]`.

Key rules enforced as errors: `possibly-unresolved-reference`, `invalid-argument-type`, `missing-argument`, `unsupported-operator`, `division-by-zero`.

Run: `ty check src/`.

Must pass before commit.

### Formatter (ruff)

Run: `ruff format src/`.

Must pass before commit.

## Import Rules

### Absolute Only

Always `from aggre.module import Name`. Never relative (`from . import`, `from .. import`).

Why: absolute imports are grep-friendly, unambiguous, and don't break when files move within the package.

### Import Order

Enforced by ruff `I` rule (isort-compatible).

Order: (1) `from __future__ import annotations`, (2) stdlib, (3) third-party, (4) local.

Blank line between each group.

### No __init__.py Re-exports

`__init__.py` is for package identity only. Contains at most `__all__`.

Import from the defining module, not from a package.

Why: re-exports hide the actual location, create coupling, and break when modules are reorganized.

## Error Handling

### Fail-Soft for Batch Operations

Catch per-item errors, log, continue to next item. Never let one failed item abort the batch.

Always log with `log.exception()` (captures stack trace).

Why: batch operations process hundreds of items. One bad URL shouldn't stop the pipeline.

### Structured Logging on Every Catch

Every `except` block must log. No silent swallows.

Use dot-notation events: `{module}.{event}` (e.g., `hackernews.fetch_failed`).

All context as keyword arguments. Never f-strings in log messages.

### Retry at Wrapper Level

Retry logic (tenacity) goes inside bronze-aware wrappers, not in business logic.

Business code calls the wrapper. Wrapper handles retries and caching.

Why: separates retry policy from business logic. Retry parameters are infrastructure concern.

## Async and Concurrency

**Async is the aspirational default for I/O-bound code.** The app trends toward async over time.

New I/O-bound modules should be async (`async def`) when the libraries support it and the overhead is reasonable. Existing sync code migrates to async incrementally — no big-bang rewrite.

Protocols and public interfaces migrate to async as their callers support it. During transition, sync wrappers (`asyncio.run()`) bridge async implementations into sync callers.

Don't add async when it provides no benefit: single blocking call with no concurrency opportunity, CPU-bound work, simple scripts.

Parallel independent work (current pattern): `ThreadPoolExecutor(max_workers=N)` + `as_completed(futures)`. Acceptable during sync-to-async migration. Target state: `asyncio.gather()` once callers are async.

Why async: I/O-bound collectors spend most time waiting on HTTP responses and DB queries. Async handles concurrency without thread overhead.

Why not force it everywhere: async adds complexity to testing, debugging, and stack traces. Only worth it when there's actual concurrent I/O. CPU-bound work (text extraction, transcription) stays sync.

Why incremental migration: rewriting working sync code is risk without reward. New code sets the direction; old code migrates when touched.

## Resource Management

Prefer `with` statements (context managers) for any resource with a lifecycle: DB connections, HTTP clients, file handles, temporary directories.

Why: context managers guarantee cleanup on exceptions. Manual `try/finally` is error-prone (forgetting the `finally`, closing in wrong order).

## Function Design

### Explicit Dependencies

Pass all dependencies as parameters. No global state, no module-level singletons.

Standard signature for DB operations: `(engine: sa.engine.Engine, ..., log: BoundLogger)`.

Standard signature for collectors: `(engine, config, settings, log)`.

Why: explicit dependencies are testable (pass mocks) and traceable (grep for callers).

### Size Limits

Target: 10-50 lines per function. Extract helpers prefixed with `_` when exceeding ~50 lines.

Helpers are private to the module (leading underscore).

### Return Conventions

- Collection/processing: return `int` (count of items processed).
- Aggregation: return `dict[str, int]`.
- DB writes: return `None`.
- Lookup: return `T | None` (None = not found).

## What to Avoid and Why

- **Circular imports**: signals wrong module boundaries. Extract shared types to leaf module.
- **`__init__.py` with logic**: creates implicit coupling. Only `__all__` allowed.
- **`Any` in function signatures**: disables type checking downstream. Use Protocol, TypeVar, or concrete types.
- **Relative imports**: break on file moves, harder to grep. Use absolute.
- **Silent exception swallowing**: `except: pass` or `except Exception: continue` without logging. Always log.
- **Global mutable state**: module-level variables mutated at runtime. Pass state explicitly.
- **God modules**: files over ~400 lines doing multiple unrelated things. Split along business boundaries.
- **Shared mutable config**: passing dicts around and mutating them. Use frozen Pydantic models.
- **Abstract base classes for interfaces**: use Protocol unless you need shared implementation.
- **Nested classes or functions**: define at module level for discoverability and testability. Exception: decorator factories (e.g., `worker_options`) — the inner `decorator(f)` function is inherent to the pattern and cannot be extracted.
- **Auto-discovery for config composition**: loses static typing, convention-based magic that silently fails. Use explicit imports.
- **Separate top-level packages for modules in same repo**: plugin ecosystem pattern, wrong for monorepo applications. Use sub-packages.
- **Layer-2 modules importing each other**: business modules must be independent. Extract shared code to layer 1.
- **Mutable `set` for constants**: use `frozenset`. Prevents accidental `.add()` / `.remove()`.
- **Plain `Enum` for string-valued enums**: use `StrEnum`. Avoids `.value` everywhere.
- **Sync for new I/O-bound code**: async is the default direction. Don't write new sync I/O code unless there's no concurrency benefit.
- **Big-bang async rewrite**: migrate incrementally. Bridge with `asyncio.run()` during transition.
- **Manual `try/finally` for resource cleanup**: use context managers. Exception-safe by default.

## Decision Tree

- Where does source code live? → `src/{package}/`
- One package or many? → One application package, sub-packages by bounded context
- File or package? → Single file <200 lines; package when separate config needed or >300 lines
- Need shared interface? → Protocol (unless shared implementation needed → ABC)
- Module growing beyond 300 lines? → Promote to package
- Two modules importing each other? → Extract shared types to leaf module (layer 1)
- External data structure? → Pydantic model or TypedDict (never raw dict)
- Config differs per environment? → `BaseSettings` (env vars)
- Config defines what the app does? → `BaseModel` (YAML)
- Adding a new collector? → Create sub-package, add to config.py + registry + CLI explicitly
- Need retry? → tenacity inside bronze-aware wrapper
- Need error recovery? → fail-soft with per-item catch + structured logging
- Pure utility function? → type-annotated, no side effects, in layer 1
- Enum needed? → `StrEnum` with lowercase values
- Constant set for membership testing? → `frozenset`
- New I/O-bound module? → async by default. Sync only if no concurrency benefit.
- Async module called from sync caller? → `asyncio.run()` bridge during migration.
- Parallel independent work (sync callers)? → `ThreadPoolExecutor` + `as_completed`. Target: `asyncio.gather()`.
- CPU-bound work? → sync. Async adds nothing here.
- Resource with lifecycle? → `with` statement (context manager)

## Maintaining These Guidelines

Same rules as `docs/medallion-guidelines.md`:

- **Audience**: coding agents, not humans. No code examples — agents generate code from patterns described here. Prescribe the **pattern**, not the **mechanism**.
- **Prescriptive over descriptive**: state what to do and when. Every section answers "what do I pick?" not "what exists?".
- **Rationale is mandatory**: every prescription includes **why** and **why not alternatives**. Prevents re-litigating settled decisions.
- **Reframe, don't patch**: when reality shows a rule is too strict, reframe the rule to match the actual invariant — don't add exceptions.
- **Decision tree as index**: every new pattern or either/or choice gets a Decision Tree entry.
- **Anti-patterns pull their weight**: "What to Avoid" entries must state why it's bad and what to do instead.
- **Keep it flat**: no nested sub-sub-sections. Each section is a self-contained reference.
