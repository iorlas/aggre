# Jina Reader Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Jina Reader as the final fallback in the webpage download chain (after direct/Browserless and Wayback), storing markdown directly as extracted text.

**Architecture:** Jina fallback lives in `download_one` (not `_download_one`). When both primary fetch and Wayback fail, Jina is attempted for non-skipped domains. On success, markdown is stored in bronze (`.md`) and written directly to `silver_content.text`, bypassing the extract task.

**Tech Stack:** httpx (already a dependency), Jina Reader free API (`r.jina.ai`)

**Spec:** `docs/superpowers/specs/2026-03-29-jina-reader-fallback-design.md`

---

### Task 1: Add `jina_reader_url` setting

**Files:**
- Modify: `src/aggre/settings.py:26` (after `browserless_url`)
- Modify: `tests/test_settings.py`
- Modify: `tests/factories/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_settings.py` inside `TestSettings`:

```python
def test_jina_reader_url_default(self, tmp_path, monkeypatch):
    """Settings().jina_reader_url == 'https://r.jina.ai' by default."""
    monkeypatch.chdir(tmp_path)
    _clear_aggre_env(monkeypatch)

    s = Settings()

    assert s.jina_reader_url == "https://r.jina.ai"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings.py::TestSettings::test_jina_reader_url_default -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'jina_reader_url'`

- [ ] **Step 3: Add the setting**

In `src/aggre/settings.py`, add after line 26 (`browserless_url: str = ""`):

```python
jina_reader_url: str = "https://r.jina.ai"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings.py::TestSettings::test_jina_reader_url_default -v`
Expected: PASS

- [ ] **Step 5: Update test factory**

In `tests/factories/config.py`, add `jina_reader_url` parameter to `make_config`:

Add parameter to function signature (after `browserless_url: str = ""`):
```python
jina_reader_url: str = "https://r.jina.ai",
```

Add to the `Settings(...)` constructor inside `make_config` (after `browserless_url=browserless_url,`):
```python
jina_reader_url=jina_reader_url,
```

- [ ] **Step 6: Update `.env.example`**

Add after the `# Proxy (optional)` section at the bottom:

```bash
# Jina Reader (optional — fallback for webpage extraction)
# AGGRE_JINA_READER_URL=https://r.jina.ai
```

- [ ] **Step 7: Run full settings tests**

Run: `uv run pytest tests/test_settings.py -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add src/aggre/settings.py tests/test_settings.py tests/factories/config.py .env.example
git commit -m "feat: add jina_reader_url setting"
```

---

### Task 2: Add `_fetch_via_jina` function and `JINA_SKIP_DOMAINS`

**Files:**
- Modify: `src/aggre/workflows/webpage.py` (add function + constant after `_fetch_via_wayback`)
- Modify: `tests/workflows/test_webpage.py` (add unit tests for `_fetch_via_jina`)

- [ ] **Step 1: Write the failing tests**

Add a new test class to `tests/workflows/test_webpage.py`. Add the import at the top alongside existing imports from `aggre.workflows.webpage`:

```python
from aggre.workflows.webpage import _fetch_via_jina, JINA_SKIP_DOMAINS, download_one, extract_one
```

Then add the test class:

```python
class TestFetchViaJina:
    """Tests for the Jina Reader fallback function."""

    def test_returns_markdown_on_success(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/article").respond(
            text="# Article Title\n\nSome article content that is long enough to pass the length check easily.",
            headers={"content-type": "text/plain"},
        )

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/article", "https://r.jina.ai")

        assert result is not None
        assert "Article Title" in result

    def test_returns_none_on_http_error(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/broken").respond(status_code=500)

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/broken", "https://r.jina.ai")

        assert result is None

    def test_returns_none_on_empty_response(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/empty").respond(text="   ")

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/empty", "https://r.jina.ai")

        assert result is None

    def test_returns_none_on_short_response(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/short").respond(text="Blocked")

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/short", "https://r.jina.ai")

        assert result is None

    def test_returns_none_on_connection_error(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/down").mock(
            side_effect=Exception("Connection refused"),
        )

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/down", "https://r.jina.ai")

        assert result is None

    def test_skip_domains_includes_reddit_hn_lobsters(self):
        assert "reddit.com" in JINA_SKIP_DOMAINS
        assert "www.reddit.com" in JINA_SKIP_DOMAINS
        assert "old.reddit.com" in JINA_SKIP_DOMAINS
        assert "news.ycombinator.com" in JINA_SKIP_DOMAINS
        assert "lobste.rs" in JINA_SKIP_DOMAINS
```

Also add the import for `create_http_client` at the top of the test file:

```python
from aggre.utils.http import create_http_client
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/workflows/test_webpage.py::TestFetchViaJina -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_via_jina'`

- [ ] **Step 3: Implement `_fetch_via_jina` and `JINA_SKIP_DOMAINS`**

In `src/aggre/workflows/webpage.py`, add after the `_fetch_via_wayback` function (after line 73):

```python
JINA_SKIP_DOMAINS = frozenset({
    "reddit.com",
    "old.reddit.com",
    "www.reddit.com",
    "news.ycombinator.com",
    "lobste.rs",
})


def _fetch_via_jina(client: httpx.Client, url: str, jina_reader_url: str) -> str | None:
    """Fetch page content via Jina Reader. Returns markdown or None."""
    try:
        resp = client.get(f"{jina_reader_url}/{url}", timeout=30.0)
        resp.raise_for_status()
        text = resp.text
        # Jina returns 200 even when target returns errors — check for empty/error content
        if not text or len(text.strip()) < 50:
            return None
        return text
    except Exception:  # noqa: BLE001 — Jina is best-effort, any failure returns None
        logger.debug("jina.unavailable url=%s", url)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/workflows/test_webpage.py::TestFetchViaJina -v`
Expected: All 6 pass

- [ ] **Step 5: Commit**

```bash
git add src/aggre/workflows/webpage.py tests/workflows/test_webpage.py
git commit -m "feat: add _fetch_via_jina function and JINA_SKIP_DOMAINS"
```

---

### Task 3: Wire Jina fallback into `download_one`

**Files:**
- Modify: `src/aggre/workflows/webpage.py` (`download_one` function, lines 226-266)
- Modify: `tests/workflows/test_webpage.py` (add integration tests)

- [ ] **Step 1: Write the failing tests**

Add a new test class to `tests/workflows/test_webpage.py`:

```python
class TestJinaFallback:
    """Tests for Jina Reader fallback in download_one."""

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch("aggre.workflows.webpage._fetch_via_jina")
    def test_jina_fallback_on_download_failure(self, mock_jina, _mock_bronze, engine, mock_http):
        """When direct fetch fails and Wayback returns None, Jina is tried."""
        config = make_config(jina_reader_url="https://r.jina.ai")
        content_id = seed_content(engine, "https://example.com/jina-fallback-test", domain="example.com")

        mock_http.get("https://example.com/jina-fallback-test").mock(
            side_effect=Exception("Connection refused"),
        )
        mock_jina.return_value = "# Fallback Article\n\nContent extracted via Jina Reader with enough text."

        result = download_one(engine, config, content_id)

        assert result.status == "downloaded_jina"
        mock_jina.assert_called_once()

        # text should be written directly
        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent).where(SilverContent.id == content_id)).fetchone()
            assert row.text is not None
            assert "Fallback Article" in row.text

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch("aggre.workflows.webpage._fetch_via_jina", return_value=None)
    def test_raises_when_jina_also_fails(self, _mock_jina, _mock_bronze, engine, mock_http):
        """When all fallbacks fail, exception propagates for Hatchet retry."""
        config = make_config(jina_reader_url="https://r.jina.ai")
        content_id = seed_content(engine, "https://example.com/all-fail", domain="example.com")

        mock_http.get("https://example.com/all-fail").mock(
            side_effect=Exception("Connection refused"),
        )

        with pytest.raises(Exception, match="Connection refused"):
            download_one(engine, config, content_id)

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch("aggre.workflows.webpage._fetch_via_jina")
    def test_skips_jina_for_reddit_domain(self, mock_jina, _mock_bronze, engine, mock_http):
        """Jina is not attempted for domains in JINA_SKIP_DOMAINS."""
        config = make_config(jina_reader_url="https://r.jina.ai")
        content_id = seed_content(engine, "https://reddit.com/r/test/comments/abc", domain="reddit.com")

        mock_http.get("https://reddit.com/r/test/comments/abc").mock(
            side_effect=Exception("Connection refused"),
        )

        with pytest.raises(Exception, match="Connection refused"):
            download_one(engine, config, content_id)

        mock_jina.assert_not_called()

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch("aggre.workflows.webpage._fetch_via_jina")
    def test_skips_jina_when_disabled(self, mock_jina, _mock_bronze, engine, mock_http):
        """Jina is not attempted when jina_reader_url is empty."""
        config = make_config(jina_reader_url="")
        content_id = seed_content(engine, "https://example.com/jina-disabled", domain="example.com")

        mock_http.get("https://example.com/jina-disabled").mock(
            side_effect=Exception("Connection refused"),
        )

        with pytest.raises(Exception, match="Connection refused"):
            download_one(engine, config, content_id)

        mock_jina.assert_not_called()

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch("aggre.workflows.webpage._fetch_via_jina")
    def test_jina_fallback_stores_bronze_as_md(self, mock_jina, _mock_bronze, engine, mock_http):
        """Jina markdown is stored in bronze with .md extension."""
        config = make_config(jina_reader_url="https://r.jina.ai")
        content_id = seed_content(engine, "https://example.com/jina-bronze-test", domain="example.com")

        mock_http.get("https://example.com/jina-bronze-test").mock(
            side_effect=Exception("Connection refused"),
        )
        mock_jina.return_value = "# Bronze Test\n\nMarkdown content stored in bronze with enough length."

        with patch("aggre.workflows.webpage.write_bronze_by_url") as mock_write_bronze:
            download_one(engine, config, content_id)
            mock_write_bronze.assert_called_once_with(
                "webpage",
                "https://example.com/jina-bronze-test",
                "response",
                "# Bronze Test\n\nMarkdown content stored in bronze with enough length.",
                "md",
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/workflows/test_webpage.py::TestJinaFallback -v`
Expected: FAIL — `download_one` doesn't try Jina yet, so tests expecting `downloaded_jina` status fail

- [ ] **Step 3: Wire Jina into `download_one`**

Replace the `except` block in `download_one` (lines 263-266 of `src/aggre/workflows/webpage.py`):

Current code:
```python
        except Exception:
            if proxy_api_url and proxy_addr:
                report_failure(proxy_api_url, proxy_addr)
            raise
```

New code:
```python
        except Exception:
            if proxy_api_url and proxy_addr:
                report_failure(proxy_api_url, proxy_addr)

            # Jina Reader fallback — last resort after direct + Wayback both failed
            jina_reader_url = config.settings.jina_reader_url or ""
            if jina_reader_url and row.domain not in JINA_SKIP_DOMAINS:
                jina_md = _fetch_via_jina(client, row.canonical_url, jina_reader_url)
                if jina_md is not None:
                    write_bronze_by_url("webpage", row.canonical_url, "response", jina_md, "md")
                    update_content(engine, content_id, text=jina_md)
                    logger.info("webpage_downloader.jina_fallback url=%s", row.canonical_url)
                    return StepOutput(status="downloaded_jina", url=row.canonical_url)
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/workflows/test_webpage.py::TestJinaFallback -v`
Expected: All 5 pass

- [ ] **Step 5: Run all webpage tests**

Run: `uv run pytest tests/workflows/test_webpage.py -v`
Expected: All pass (existing tests unaffected)

- [ ] **Step 6: Commit**

```bash
git add src/aggre/workflows/webpage.py tests/workflows/test_webpage.py
git commit -m "feat: wire Jina Reader fallback into download_one"
```

---

### Task 4: Lint and final check

**Files:** None new — validation only.

- [ ] **Step 1: Run lint**

Run: `make lint`
Expected: Pass with no errors

- [ ] **Step 2: Run full test suite**

Run: `make test`
Expected: All pass

- [ ] **Step 3: Run diff coverage**

Run: `make coverage-diff`
Expected: New lines covered (the `_fetch_via_jina` function has `pragma: no cover` since it hits an external service, but `download_one` changes are covered by the new tests)

- [ ] **Step 4: Commit any lint fixes if needed**

```bash
git add -u
git commit -m "fix: lint fixes for jina fallback"
```
