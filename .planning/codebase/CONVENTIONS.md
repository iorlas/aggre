# Coding Conventions

**Analysis Date:** 2026-02-20

## Naming Patterns

**Files:**
- Lowercase with underscores: `content_fetcher.py`, `enrichment.py`
- Module names match domain concepts: `collectors/`, `urls.py`, `statuses.py`
- Collector classes: `{Source}Collector` (e.g., `HackernewsCollector`, `RedditCollector` in `src/aggre/collectors/`)

**Functions:**
- Lowercase with underscores: `normalize_url()`, `extract_domain()`, `ensure_content()`
- Internal/private functions prefixed with single underscore: `_download_one()`, `_update_content()`, `_upsert_discussion()`
- State transition functions are verb phrases: `content_skipped()`, `content_downloaded()`, `content_fetched()`, `content_fetch_failed()` (see `src/aggre/content_fetcher.py`)
- Data factory functions in tests prefixed with `_make_`: `_make_config()`, `_make_hit()`, `_make_search_response()` (e.g., `tests/test_hackernews.py`)

**Variables:**
- Lowercase with underscores: `total_new`, `batch_limit`, `max_workers`
- IDs named explicitly: `content_id`, `discussion_id`, `source_id`, `external_id` (NOT `ci_id` or `post_id`)
- Loop variables: `row`, `conn`, `engine`
- Config/logging objects: `config`, `log`, `engine`

**Types:**
- Classes use PascalCase: `BronzeDiscussion`, `SilverDiscussion`, `SilverContent`, `Source` (database models in `src/aggre/db.py`)
- Enums use PascalCase and derive from `StrEnum`: `FetchStatus`, `TranscriptionStatus`, `CommentsStatus` (see `src/aggre/statuses.py`)
- Pydantic models use PascalCase: `Settings`, `AppConfig`, `RssSource`, `RedditSource` (see `src/aggre/config.py`)
- Protocol classes use PascalCase: `Collector`, `SearchableCollector` (see `src/aggre/collectors/base.py`)

## Code Style

**Formatting:**
- Tool: ruff (configured in `pyproject.toml`)
- Line length: 140 characters (`line-length = 140`)
- Python version: 3.12+ (`requires-python = ">=3.12"`)
- Future imports: `from __future__ import annotations` at top of every module for forward reference support

**Linting:**
- Tool: ruff with rule set `["E", "F", "I", "N", "W", "UP"]`
  - E: pycodestyle errors
  - F: pyflakes errors
  - I: isort (import sorting)
  - N: pep8-naming
  - W: pycodestyle warnings
  - UP: pyupgrade

## Import Organization

**Order:**
1. `from __future__ import annotations` (always first)
2. Standard library: `import json`, `import time`, `from datetime import UTC, datetime`
3. Third-party: `import httpx`, `import sqlalchemy as sa`, `import structlog`
4. Local: `from aggre.collectors.base import BaseCollector`, `from aggre.config import AppConfig`

**Path Aliases:**
- Absolute imports from package root: `from aggre.config import AppConfig` (NOT relative imports)
- No top-level `__init__.py` re-exports; imports always specify the module

**Example from `src/aggre/content_fetcher.py`:**
```python
from __future__ import annotations

import concurrent.futures

import httpx
import sqlalchemy as sa
import structlog
import trafilatura

from aggre.config import AppConfig
from aggre.db import SilverContent, _update_content, now_iso
from aggre.http import create_http_client
from aggre.statuses import FetchStatus
```

## Error Handling

**Patterns:**
- Use broad `except Exception` for external service calls; log with `.exception()` to capture stack trace
- Example from `src/aggre/enrichment.py`:
  ```python
  try:
      hn_found = hn_collector.search_by_url(content_url, engine, config, log)
      totals["hackernews"] += hn_found
  except Exception:
      log.exception("enrich.hn_search_failed", url=content_url)
      failed = True
  ```
- Use tenacity for retry logic on HTTP calls: `@retry`, `stop_after_attempt()`, `wait_exponential()` (see `src/aggre/collectors/reddit.py`)
- State transitions record errors in database: `content_fetch_failed(engine, content_id, error=str(exc))`
- Batch operations catch per-item errors but continue processing (fail-soft approach)

## Logging

**Framework:** structlog with dual output (JSON to file, human-readable to stdout)

**Setup:** `src/aggre/logging.py` - `setup_logging(log_dir: str, log_name: str)`

**Patterns:**
- Log level context set once at initialization; all code uses bound logger
- Event names use dot notation: `{component}.{event}` (e.g., `content_fetcher.downloaded`, `enrich.hn_search_failed`, `reddit.rate_limit_exhausted`)
- Info level for normal flow: `log.info("content_fetcher.download_starting", batch_size=len(rows))`
- Warning level for recoverable issues: `log.warning("reddit.rate_limit_exhausted", remaining=remaining_f, sleeping=reset_f)`
- Exception level for errors: `log.exception("content_fetcher.download_failed", url=url)` (includes stack trace)
- All contextual data passed as keyword arguments: `log.info("event", key=value, key2=value2)`
- Never use f-strings in log messages; always use structured fields

## Comments

**When to Comment:**
- Docstrings on all public functions, classes, and modules
- Explain "why" not "what" — code should be self-explanatory for "what"
- Inline comments for non-obvious logic or business rules
- Section comments for file organization: `# -- Fetch state transitions --` (see `src/aggre/content_fetcher.py`)

**JSDoc/TSDoc:**
- Use docstrings for all functions (PEP 257 style)
- One-liner for simple functions: `"""Current UTC time as ISO 8601 string."""` (see `src/aggre/db.py`)
- Multi-line for complex functions with parameters and return descriptions

**Example from `src/aggre/urls.py`:**
```python
def normalize_url(url: str) -> str | None:
    """Normalize URLs for content deduplication.

    Removes tracking params, normalizes domain/path, handles domain-specific rules.
    Returns None for non-HTTP schemes or empty strings.
    """
```

## Function Design

**Size:** Functions typically 10-50 lines; long functions factored into helpers with leading underscore

**Parameters:**
- Explicit over implicit: use keyword-only arguments for clarity (`def func(*, body_text: str)`)
- Database operations always take `engine: sa.engine.Engine` and `log: structlog.stdlib.BoundLogger`
- Collectors always take `(engine, config, log)`
- Batch operations take `batch_limit: int = 50` parameter with sensible default

**Return Values:**
- Return counts for collection operations: `int` (number of items processed)
- Return None on no-op or duplicate detection: `int | None`
- Return dicts for aggregations: `dict[str, int]` (see `enrich_content_discussions()` returns `{"hackernews": 2, "lobsters": 1}`)
- Database update functions return `None`

**Example from `src/aggre/collectors/base.py`:**
```python
def _store_raw_item(self, conn: sa.Connection, ext_id: str, raw_data: Any) -> int | None:
    """Insert a BronzeDiscussion. Returns id if new, None if duplicate."""
    stmt = pg_insert(BronzeDiscussion).values(...)
    stmt = stmt.on_conflict_do_nothing(index_elements=["source_type", "external_id"])
    result = conn.execute(stmt)
    if result.rowcount == 0:
        return None
    return result.inserted_primary_key[0]
```

## Module Design

**Exports:**
- All public functions/classes defined at module level (no nested classes)
- Internal helpers prefixed with `_` and used only within the module
- Collectors implemented as classes inheriting from `BaseCollector` (see `src/aggre/collectors/`)
- Config classes use Pydantic `BaseModel` and `BaseSettings` (see `src/aggre/config.py`)

**Barrel Files:**
- Not used; always import directly from target module
- Tests import directly: `from aggre.content_fetcher import download_content, extract_html_text`

## Type Hints

**Always used:**
- Function signatures fully typed: `def func(a: int, b: str) -> bool:`
- Union types use `|`: `int | None` not `Optional[int]`
- Return type on all functions (use `-> None` for void)
- Generic containers typed: `dict[str, Any]`, `list[str]`, `Sequence[str]`

**Example from `src/aggre/db.py`:**
```python
class SilverContent(Base):
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_url: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
```

---

*Convention analysis: 2026-02-20*
