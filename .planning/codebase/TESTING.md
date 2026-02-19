# Testing Patterns

**Analysis Date:** 2026-02-20

## Test Framework

**Runner:**
- pytest 8.4.2+ (configured in `pyproject.toml`)
- Config: `[tool.pytest.ini_options]` in `pyproject.toml`
  - Test paths: `testpaths = ["tests"]`
  - Test file pattern: `python_files = ["test_*.py"]`
  - Test class pattern: `python_classes = ["Test*"]`
  - Test function pattern: `python_functions = ["test_*"]`
  - Options: `-v --strict-markers` (verbose, enforce marker registration)
  - Python path: current directory (`.`)

**Assertion Library:**
- pytest built-in assertions (no separate assertion library)
- Simple equality checks: `assert count == 1`, `assert row.external_id == "12345"`
- Exception checking: `with pytest.raises(Exception):`

**Run Commands:**
```bash
pytest tests/                          # Run all tests
pytest tests/ -v                       # Verbose output
pytest tests/test_urls.py             # Run single file
pytest tests/ -m unit                 # Run tests marked as unit
pytest tests/ --recording-mode=once   # pytest-recording for VCR
```

## Test File Organization

**Location:**
- Co-located in `tests/` directory parallel to `src/aggre/`
- Tests import directly from package: `from aggre.collectors.hackernews import HackernewsCollector`

**Naming:**
- Test files: `test_*.py` (e.g., `test_urls.py`, `test_hackernews.py`, `test_content.py`)
- Test classes: `Test*` (e.g., `TestNormalizeUrl`, `TestHackernewsCollectorDiscussions`)
- Test functions: `test_*` (e.g., `test_basic_normalization()`, `test_stores_posts()`)

**Structure:**
```
tests/
├── conftest.py                        # Shared fixtures (session-scoped engine, autouse clean_tables)
├── test_urls.py                       # URL normalization tests (unit)
├── test_content.py                    # Content fetcher tests (unit)
├── test_hackernews.py                 # Hacker News collector tests (unit + integration)
├── test_reddit.py                     # Reddit collector tests
├── test_enrichment.py                 # Enrichment module tests
├── test_acceptance_pipeline.py        # Full pipeline acceptance tests
├── test_acceptance_cli.py             # CLI acceptance tests
└── test_acceptance_content_linking.py # Content linking acceptance tests
```

## Test Structure

**Suite Organization:**
```python
class TestNormalizeUrl:
    def test_basic_normalization(self):
        assert normalize_url("  HTTP://WWW.Example.COM/page/  ") == "https://example.com/page"

    def test_strips_tracking_params(self):
        result = normalize_url("https://example.com/page?utm_source=twitter&utm_medium=social&real=1")
        assert result == "https://example.com/page?real=1"

    # More test methods...
```

**Patterns:**

1. **Setup (Arrange):** Create test data using helper functions
   ```python
   def test_stores_posts(self, engine):
       config = _make_config()
       log = MagicMock()
       collector = HackernewsCollector()

       hit = _make_hit()
       responses = {"search_by_date": _make_search_response(hit)}
   ```

2. **Execution (Act):** Call the function under test with mock/patch context
   ```python
       with patch("aggre.collectors.hackernews.httpx.Client") as mock_cls, \
            patch("aggre.collectors.hackernews.time.sleep"):
           mock_cls.return_value = _mock_httpx_client(responses)
           count = collector.collect(engine, config, log)
   ```

3. **Assertion (Assert):** Verify outcomes in database or mock calls
   ```python
       assert count == 1
       with engine.connect() as conn:
           raws = conn.execute(sa.select(BronzeDiscussion)).fetchall()
           assert len(raws) == 1
           assert raws[0].external_id == "12345"
   ```

**Teardown Pattern:**
- Handled by `clean_tables` fixture (autouse): truncates all tables before each test
- No manual cleanup needed; database state is reset per-test

## Mocking

**Framework:** unittest.mock (from stdlib)

**Patterns:**

1. **HTTP Client Mocks:** Create factory functions that return configured mock clients
   ```python
   def _mock_httpx_client(responses: dict):
       """Create a mock httpx.Client that returns configured responses based on URL patterns."""
       client = MagicMock()

       def fake_get(url):
           resp = MagicMock()
           resp.status_code = 200
           for pattern, data in responses.items():
               if pattern in url:
                   resp.json.return_value = data
                   return resp
           resp.json.return_value = {"hits": []}
           return resp

       client.get.side_effect = fake_get
       return client
   ```
   (From `tests/test_hackernews.py`)

2. **Patching External Libraries:** Use `patch()` context manager
   ```python
   with patch("aggre.collectors.hackernews.httpx.Client") as mock_cls:
       mock_cls.return_value = _mock_httpx_client(responses)
       count = collector.collect(engine, config, log)
   ```

3. **Mocking Config/Logger:** Use MagicMock directly
   ```python
   log = MagicMock()
   config = _make_config()  # Uses Pydantic AppConfig
   ```

4. **Verifying Mock Calls:**
   ```python
   mock_hn.search_by_url.assert_called_once_with(
       "https://example.com/article", engine, config, log
   )
   mock_hn.search_by_url.assert_not_called()
   ```

**What to Mock:**
- External HTTP clients: httpx.Client (patch at import site in module under test)
- Time.sleep for rate limiting
- External service APIs (Reddit, Hacker News, etc.)
- Do NOT mock database operations; use real PostgreSQL test database

**What NOT to Mock:**
- Database: use real test database with `engine` fixture
- SQLAlchemy models: instantiate real instances
- Local utilities: use real implementations
- Logging: use MagicMock to verify log calls if needed

## Fixtures and Factories

**Test Data:**

1. **Config Factories:** Helper functions creating test AppConfig instances
   ```python
   def _make_config(rate_limit: float = 0.0) -> AppConfig:
       return AppConfig(
           hackernews=[HackernewsSource(name="Hacker News")],
           settings=Settings(hn_rate_limit=rate_limit),
       )
   ```

2. **API Response Factories:** Create realistic API responses for mocking
   ```python
   def _make_hit(object_id: str = "12345", title: str = "Test Story", author: str = "pg", url: str = "https://example.com/article"):
       return {
           "objectID": object_id,
           "title": title,
           "author": author,
           "url": url,
           "points": 100,
           "num_comments": 25,
           "created_at": "2024-01-15T12:00:00.000Z",
       }
   ```

3. **Database Seeders:** Insert test data using PostgreSQL INSERT helpers
   ```python
   def _seed_content(engine, url: str, domain: str | None = None, fetch_status: str = "pending", raw_html: str | None = None):
       with engine.begin() as conn:
           stmt = pg_insert(SilverContent).values(
               canonical_url=url,
               domain=domain,
               fetch_status=fetch_status,
               raw_html=raw_html,
           )
           stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
           result = conn.execute(stmt)
           return result.inserted_primary_key[0]
   ```
   (From `tests/test_content.py`)

**Location:**
- Helper functions defined in test modules themselves (NOT in fixtures)
- Prefixed with `_` to indicate test-local scope: `_make_config()`, `_seed_content()`, `_mock_httpx_client()`
- Shared fixtures (engine, clean_tables) in `tests/conftest.py`

## Coverage

**Requirements:** Not enforced in CI (no coverage threshold set)

**View Coverage:**
```bash
pytest tests/ --cov=aggre --cov-report=html
pytest tests/ --cov=aggre --cov-report=term
```

**Current Coverage Areas:**
- Unit tests for URL normalization and extraction (`test_urls.py`)
- Collector unit tests with mocked HTTP (`test_hackernews.py`, `test_reddit.py`, etc.)
- Integration tests with real database
- Acceptance/pipeline tests for full workflows

## Test Types

**Unit Tests:**
- Scope: Individual functions (e.g., `normalize_url()`, `extract_domain()`)
- Approach: Pure function testing, no database, no HTTP
- Example: `TestNormalizeUrl` class in `test_urls.py`
- Mocking: As needed for external dependencies

**Integration Tests:**
- Scope: Collector class methods with database and mocked HTTP
- Approach: Real PostgreSQL database, mocked external APIs
- Example: `TestHackernewsCollectorDiscussions` in `test_hackernews.py`
- Mocking: HTTP clients via `patch()`, but NOT database

**Acceptance/End-to-End Tests:**
- Scope: Full pipeline workflows (collect → download → extract → enrich)
- Approach: Real database, mocked HTTP, verified state changes
- Files: `test_acceptance_*.py` (e.g., `test_acceptance_pipeline.py`, `test_acceptance_cli.py`)
- Example: Full Reddit collection + comment fetching + content linking

**Contract Tests:**
- Marker: `@pytest.mark.contract` (registered in `pyproject.toml`)
- Purpose: Verify external API contracts (Hacker News, Reddit, etc.)
- Approach: Use pytest-recording for VCR (recorded HTTP responses)
- Run: Typically CI-only with recorded responses

## Common Patterns

**Async Testing:**
- No async code in codebase; all I/O is synchronous
- HTTP calls use synchronous httpx.Client, not httpx.AsyncClient
- Threading used for parallelism (concurrent.futures), not asyncio

**Error Testing:**

1. **Exception on Operations:**
   ```python
   def test_handles_download_error(self, engine):
       config = AppConfig(settings=Settings())
       log = MagicMock()

       _seed_content(engine, "https://example.com/broken", domain="example.com")

       mock_client = MagicMock()
       mock_client.get.side_effect = Exception("Connection refused")

       with patch("aggre.content_fetcher.httpx.Client", return_value=mock_client):
           count = download_content(engine, config, log)

       assert count == 1
       with engine.connect() as conn:
           row = conn.execute(sa.select(SilverContent)).fetchone()
           assert row.fetch_status == "failed"
           assert "Connection refused" in row.fetch_error
   ```
   (From `tests/test_content.py`)

2. **Batch Operations Continue on Error:**
   - Collectors catch per-item exceptions and continue processing
   - Tests verify error count and log messages
   - Database stores error details for inspection

**Database Verification Pattern:**
```python
with engine.connect() as conn:
    rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
    assert len(rows) == 1
    assert rows[0].title == "Test Story"
    assert rows[0].comments_status == "pending"

    meta = json.loads(rows[0].meta)
    assert "hn_url" in meta
```

**Batch Limit Testing:**
```python
def test_respects_batch_limit(self, engine):
    for i in range(5):
        _seed_content(engine, f"https://youtube.com/watch?v=vid{i}", domain="youtube.com")

    count = download_content(engine, config, log, batch_limit=3)
    assert count == 3
```

## Fixtures

**Session-Scoped Engine Fixture (`conftest.py`):**
```python
@pytest.fixture(scope="session")
def engine():
    """Session-scoped PostgreSQL test engine."""
    url = os.environ.get("AGGRE_TEST_DATABASE_URL", "postgresql+psycopg2://aggre:aggre@localhost/aggre_test")
    eng = get_engine(url)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()
```

**Autouse Clean Tables Fixture (`conftest.py`):**
```python
@pytest.fixture(autouse=True)
def clean_tables(engine):
    """Truncate all tables before each test."""
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(sa.text(f"TRUNCATE TABLE {table.name} CASCADE"))
    yield
```

---

*Testing analysis: 2026-02-20*
