# GitHub Trending Collector Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub Trending collector that scrapes trending repos daily/weekly/monthly and feeds them into the Silver pipeline.

**Architecture:** New collector following the standard BaseCollector pattern. Scrapes `github.com/trending` with httpx, parses HTML with selectolax, stores raw HTML in bronze, and creates SilverDiscussion/SilverContent rows. Daily entries are append-only (new row per day); weekly/monthly use upsert semantics.

**Event emission note:** The spec suggests only daily refs should emit `item.new` events. However, downstream pipelines are idempotent (webpage workflow skips if `text IS NOT NULL`; discussion search skips if `discussions_searched_at IS NOT NULL`). Rather than modifying the generic `collect_source` function, we let all events fire — weekly/monthly events for already-known repos are harmless no-ops. This keeps the collector simple and avoids coupling `collect_source` to source-specific logic.

**Tech Stack:** httpx (existing), selectolax (new dependency), respx (testing)

**Spec:** `docs/superpowers/specs/2026-03-16-github-trending-collector-design.md`

---

## Chunk 1: Dependencies and Configuration

### Task 1: Add selectolax dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add selectolax to dependencies**

In `pyproject.toml`, add `"selectolax>=0.3.0"` to the `dependencies` list.

- [ ] **Step 2: Install and verify**

Run: `uv sync`
Expected: selectolax installs successfully

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add selectolax dependency for GitHub Trending HTML parsing"
```

### Task 2: Create GithubTrendingConfig and wire into AppConfig

**Files:**
- Create: `src/aggre/collectors/github_trending/__init__.py`
- Create: `src/aggre/collectors/github_trending/config.py`
- Modify: `src/aggre/config.py`
- Modify: `tests/factories.py`

- [ ] **Step 1: Create the config module**

Create `src/aggre/collectors/github_trending/__init__.py`:
```python
"""GitHub Trending collector."""

from __future__ import annotations
```

Create `src/aggre/collectors/github_trending/config.py`:
```python
"""GitHub Trending collector configuration."""

from __future__ import annotations

from pydantic import BaseModel


class GithubTrendingConfig(BaseModel):
    """No user-configurable fields — periods are hardcoded in the collector."""

    pass
```

- [ ] **Step 2: Add to AppConfig**

In `src/aggre/config.py`, add the import:
```python
from aggre.collectors.github_trending.config import GithubTrendingConfig
```

Add the field to `AppConfig`:
```python
github_trending: GithubTrendingConfig = GithubTrendingConfig()
```

- [ ] **Step 3: Update make_config in factories**

In `tests/factories.py`, add the import:
```python
from aggre.collectors.github_trending.config import GithubTrendingConfig
```

Add parameter to `make_config()`:
```python
github_trending: GithubTrendingConfig | None = None,
```

Add to the `AppConfig(...)` constructor:
```python
github_trending=github_trending or GithubTrendingConfig(),
```

- [ ] **Step 4: Verify tests still pass**

Run: `make test-e2e`
Expected: All existing tests pass (no regressions from config change)

- [ ] **Step 5: Commit**

```bash
git add src/aggre/collectors/github_trending/ src/aggre/config.py tests/factories.py
git commit -m "feat: add GithubTrendingConfig and wire into AppConfig"
```

---

## Chunk 2: HTML Parser

### Task 3: Build and test the HTML parser

The parser is a pure function: HTML string in → list of repo dicts out. Test it independently from the collector.

**Files:**
- Create: `src/aggre/collectors/github_trending/parser.py`
- Create: `tests/collectors/test_github_trending_parser.py`

- [ ] **Step 1: Write the test with a realistic HTML fixture**

First, fetch a real GitHub Trending page to use as a test fixture. Save a minimal but realistic HTML snippet.

Create `tests/collectors/test_github_trending_parser.py`:

```python
"""Tests for GitHub Trending HTML parser."""

from __future__ import annotations

import pytest

from aggre.collectors.github_trending.parser import parse_trending_page

pytestmark = pytest.mark.unit


# Minimal realistic HTML fixture matching GitHub's actual trending page structure.
# This was derived from a real page snapshot — update if GitHub changes their HTML.
TRENDING_HTML = """
<html>
<body>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/openai/codex" data-view-component="true">
      <span>openai /</span>
      <span class="text-normal">codex</span>
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">An AI pair programmer</p>
  <div class="f6 color-fg-muted mt-2">
    <span class="d-inline-block ml-0 mr-3">
      <span class="repo-language-color" style="background-color: #3572A5"></span>
      <span itemprop="programmingLanguage">Python</span>
    </span>
    <a class="Link--muted d-inline-block mr-3" href="/openai/codex/stargazers">
      <svg class="octicon octicon-star" aria-label="star"></svg>
      45,231
    </a>
    <a class="Link--muted d-inline-block mr-3" href="/openai/codex/forks">
      <svg class="octicon octicon-repo-forked" aria-label="fork"></svg>
      1,234
    </a>
    <span class="d-inline-block float-sm-right">
      <svg class="octicon octicon-star" aria-label="star"></svg>
      1,523 stars today
    </span>
  </div>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/rust-lang/rust">
      <span>rust-lang /</span>
      <span class="text-normal">rust</span>
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">The Rust programming language</p>
  <div class="f6 color-fg-muted mt-2">
    <span class="d-inline-block ml-0 mr-3">
      <span class="repo-language-color" style="background-color: #dea584"></span>
      <span itemprop="programmingLanguage">Rust</span>
    </span>
    <a class="Link--muted d-inline-block mr-3" href="/rust-lang/rust/stargazers">
      <svg class="octicon octicon-star" aria-label="star"></svg>
      98,765
    </a>
    <a class="Link--muted d-inline-block mr-3" href="/rust-lang/rust/forks">
      <svg class="octicon octicon-repo-forked" aria-label="fork"></svg>
      12,345
    </a>
    <span class="d-inline-block float-sm-right">
      <svg class="octicon octicon-star" aria-label="star"></svg>
      432 stars today
    </span>
  </div>
</article>
</body>
</html>
"""


class TestParseTrendingPage:
    def test_extracts_repos_from_html(self):
        repos = parse_trending_page(TRENDING_HTML)

        assert len(repos) == 2

    def test_extracts_owner_and_name(self):
        repos = parse_trending_page(TRENDING_HTML)

        assert repos[0]["owner"] == "openai"
        assert repos[0]["name"] == "codex"
        assert repos[1]["owner"] == "rust-lang"
        assert repos[1]["name"] == "rust"

    def test_extracts_description(self):
        repos = parse_trending_page(TRENDING_HTML)

        assert repos[0]["description"] == "An AI pair programmer"

    def test_extracts_language(self):
        repos = parse_trending_page(TRENDING_HTML)

        assert repos[0]["language"] == "Python"
        assert repos[1]["language"] == "Rust"

    def test_extracts_stars(self):
        repos = parse_trending_page(TRENDING_HTML)

        assert repos[0]["total_stars"] == 45231
        assert repos[1]["total_stars"] == 98765

    def test_extracts_forks(self):
        repos = parse_trending_page(TRENDING_HTML)

        assert repos[0]["forks"] == 1234

    def test_extracts_stars_in_period(self):
        repos = parse_trending_page(TRENDING_HTML)

        assert repos[0]["stars_in_period"] == 1523
        assert repos[1]["stars_in_period"] == 432

    def test_empty_html_returns_empty_list(self):
        repos = parse_trending_page("<html><body></body></html>")

        assert repos == []

    def test_missing_description_returns_empty_string(self):
        html = """
        <article class="Box-row">
          <h2 class="h3 lh-condensed">
            <a href="/owner/repo">
              <span>owner /</span>
              <span class="text-normal">repo</span>
            </a>
          </h2>
          <div class="f6 color-fg-muted mt-2">
            <span class="d-inline-block float-sm-right">
              100 stars today
            </span>
          </div>
        </article>
        """
        repos = parse_trending_page(html)

        assert repos[0]["description"] == ""

    def test_missing_language_returns_empty_string(self):
        html = """
        <article class="Box-row">
          <h2 class="h3 lh-condensed">
            <a href="/owner/repo">
              <span>owner /</span>
              <span class="text-normal">repo</span>
            </a>
          </h2>
          <div class="f6 color-fg-muted mt-2">
            <span class="d-inline-block float-sm-right">
              50 stars today
            </span>
          </div>
        </article>
        """
        repos = parse_trending_page(html)

        assert repos[0]["language"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/collectors/test_github_trending_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aggre.collectors.github_trending.parser'`

- [ ] **Step 3: Implement the parser**

Create `src/aggre/collectors/github_trending/parser.py`:

```python
"""Parse GitHub Trending HTML pages into structured repo data."""

from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)


def parse_trending_page(html: str) -> list[dict[str, object]]:
    """Extract trending repository data from GitHub Trending HTML.

    Returns a list of dicts with keys:
        owner, name, description, language, total_stars, forks, stars_in_period
    """
    tree = HTMLParser(html)
    repos: list[dict[str, object]] = []

    for article in tree.css("article.Box-row"):
        link = article.css_first("h2 a")
        if not link:
            continue

        href = link.attributes.get("href", "")
        parts = [p for p in href.strip("/").split("/") if p]
        if len(parts) < 2:
            continue

        owner = parts[0]
        name = parts[1]

        # Description
        desc_el = article.css_first("p")
        description = desc_el.text(strip=True) if desc_el else ""

        # Language
        lang_el = article.css_first("[itemprop='programmingLanguage']")
        language = lang_el.text(strip=True) if lang_el else ""

        # Total stars — first stargazers link
        total_stars = 0
        star_link = article.css_first("a[href$='/stargazers']")
        if star_link:
            total_stars = _parse_number(star_link.text(strip=True))

        # Forks
        forks = 0
        fork_link = article.css_first("a[href$='/forks']")
        if fork_link:
            forks = _parse_number(fork_link.text(strip=True))

        # Stars in period — the "N stars today/this week/this month" text
        stars_in_period = 0
        period_el = article.css_first("span.d-inline-block.float-sm-right")
        if period_el:
            stars_in_period = _parse_number(period_el.text(strip=True))

        repos.append({
            "owner": owner,
            "name": name,
            "description": description,
            "language": language,
            "total_stars": total_stars,
            "forks": forks,
            "stars_in_period": stars_in_period,
        })

    if 0 < len(repos) < 10:
        logger.warning(
            "github_trending.low_repo_count count=%d — GitHub may have changed their HTML structure",
            len(repos),
        )

    return repos


def _parse_number(text: str) -> int:
    """Parse a number string like '45,231' or '1,523 stars today' into an int."""
    match = re.search(r"[\d,]+", text)
    if not match:
        return 0
    return int(match.group().replace(",", ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/collectors/test_github_trending_parser.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/aggre/collectors/github_trending/parser.py tests/collectors/test_github_trending_parser.py
git commit -m "feat: add GitHub Trending HTML parser with selectolax"
```

---

## Chunk 3: Collector Implementation

### Task 4: Write collector tests

**Files:**
- Create: `tests/collectors/test_github_trending.py`

The collector test file follows the same pattern as `test_lobsters.py`. Mock HTTP responses with respx, verify database state after collection.

- [ ] **Step 1: Add factory function to `tests/factories.py`**

Add a `github_trending_html` factory that builds a minimal trending HTML page from a list of repo tuples:

```python
# ===========================================================================
# GitHub Trending response builders
# ===========================================================================


def github_trending_repo_html(
    owner: str = "openai",
    name: str = "codex",
    description: str = "An AI pair programmer",
    language: str = "Python",
    total_stars: str = "45,231",
    forks: str = "1,234",
    stars_in_period: str = "1,523 stars today",
) -> str:
    """Build one <article> block matching GitHub Trending HTML structure."""
    lang_span = ""
    if language:
        lang_span = f"""
        <span class="d-inline-block ml-0 mr-3">
          <span class="repo-language-color" style="background-color: #3572A5"></span>
          <span itemprop="programmingLanguage">{language}</span>
        </span>"""

    return f"""
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/{owner}/{name}">
      <span>{owner} /</span>
      <span class="text-normal">{name}</span>
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">{description}</p>
  <div class="f6 color-fg-muted mt-2">{lang_span}
    <a class="Link--muted d-inline-block mr-3" href="/{owner}/{name}/stargazers">
      <svg class="octicon octicon-star" aria-label="star"></svg>
      {total_stars}
    </a>
    <a class="Link--muted d-inline-block mr-3" href="/{owner}/{name}/forks">
      <svg class="octicon octicon-repo-forked" aria-label="fork"></svg>
      {forks}
    </a>
    <span class="d-inline-block float-sm-right">
      <svg class="octicon octicon-star" aria-label="star"></svg>
      {stars_in_period}
    </span>
  </div>
</article>"""


def github_trending_page(*repo_htmls: str) -> str:
    """Wrap repo article blocks into a full trending page."""
    body = "\n".join(repo_htmls)
    return f"<html><body>{body}</body></html>"
```

- [ ] **Step 2: Write collector integration tests**

Create `tests/collectors/test_github_trending.py`:

```python
"""Tests for GitHub Trending collector."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from aggre.collectors.github_trending.collector import GithubTrendingCollector
from tests.factories import (
    github_trending_page,
    github_trending_repo_html,
    make_config,
)
from tests.helpers import collect, get_contents, get_discussions, get_sources

pytestmark = pytest.mark.integration

TRENDING_URL = "https://github.com/trending"


@pytest.fixture()
def collector():
    return GithubTrendingCollector()


def _mock_trending_responses(mock_http, daily_html=None, weekly_html=None, monthly_html=None):
    """Helper to set up mock HTTP responses for all three periods."""
    default_html = github_trending_page(github_trending_repo_html())
    mock_http.get(
        url=f"{TRENDING_URL}?since=daily",
    ).respond(text=daily_html or default_html)
    mock_http.get(
        url=f"{TRENDING_URL}?since=weekly",
    ).respond(text=weekly_html or default_html)
    mock_http.get(
        url=f"{TRENDING_URL}?since=monthly",
    ).respond(text=monthly_html or default_html)


class TestGithubTrendingCollectDiscussions:
    def test_creates_discussions_for_all_periods(self, engine, mock_http, collector):
        repo_html = github_trending_repo_html(owner="openai", name="codex")
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            count = collect(collector, engine, config.github_trending, config.settings)

        # 1 repo × 3 periods = 3 discussions
        assert count == 3
        discussions = get_discussions(engine, source_type="github_trending")
        assert len(discussions) == 3

    def test_creates_single_content_per_repo(self, engine, mock_http, collector):
        repo_html = github_trending_repo_html(owner="openai", name="codex")
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        # All 3 discussions point to the same SilverContent
        contents = get_contents(engine, domain="github.com")
        assert len(contents) == 1
        assert "github.com/openai/codex" in contents[0].canonical_url

    def test_daily_external_id_includes_date(self, engine, mock_http, collector):
        repo_html = github_trending_repo_html(owner="openai", name="codex")
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        discussions = get_discussions(engine, source_type="github_trending")
        external_ids = [d.external_id for d in discussions]
        today = date.today().isoformat()
        assert any(f"openai/codex:daily:{today}" == eid for eid in external_ids)

    def test_stores_score_as_stars_in_period(self, engine, mock_http, collector):
        repo_html = github_trending_repo_html(stars_in_period="1,523 stars today")
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        discussions = get_discussions(engine, source_type="github_trending")
        daily = [d for d in discussions if "daily" in d.external_id]
        assert daily[0].score == 1523

    def test_stores_meta_with_total_stars_forks_language_period(self, engine, mock_http, collector):
        repo_html = github_trending_repo_html(
            language="Python", total_stars="45,231", forks="1,234",
        )
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        discussions = get_discussions(engine, source_type="github_trending")
        daily = [d for d in discussions if "daily" in d.external_id]
        meta = json.loads(daily[0].meta)
        assert meta["total_stars"] == 45231
        assert meta["forks"] == 1234
        assert meta["language"] == "Python"
        assert meta["period"] == "daily"

    def test_multiple_repos_on_page(self, engine, mock_http, collector):
        page = github_trending_page(
            github_trending_repo_html(owner="openai", name="codex"),
            github_trending_repo_html(owner="rust-lang", name="rust"),
        )
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            count = collect(collector, engine, config.github_trending, config.settings)

        # 2 repos × 3 periods = 6 discussions
        assert count == 6

    def test_creates_source_row(self, engine, mock_http, collector):
        page = github_trending_page(github_trending_repo_html())
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        sources = get_sources(engine, type="github_trending")
        assert len(sources) == 1
        assert sources[0].name == "GitHub Trending"

    def test_sets_author_to_repo_owner(self, engine, mock_http, collector):
        repo_html = github_trending_repo_html(owner="torvalds", name="linux")
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        discussions = get_discussions(engine, source_type="github_trending")
        assert all(d.author == "torvalds" for d in discussions)

    def test_sets_title_to_description(self, engine, mock_http, collector):
        repo_html = github_trending_repo_html(description="An AI pair programmer")
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        discussions = get_discussions(engine, source_type="github_trending")
        assert all(d.title == "An AI pair programmer" for d in discussions)


class TestGithubTrendingUpsertSemantics:
    def test_daily_is_append_only(self, engine, mock_http, collector):
        """Running daily collection twice on the same day creates only one row (external_id conflict)."""
        repo_html = github_trending_repo_html(owner="openai", name="codex")
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)
            collect(collector, engine, config.github_trending, config.settings)

        discussions = get_discussions(engine, source_type="github_trending")
        daily = [d for d in discussions if "daily" in d.external_id]
        assert len(daily) == 1  # Same external_id, no duplicate

    def test_weekly_upserts_score_and_published_at(self, engine, mock_http, collector):
        """Weekly collection updates score and published_at on re-run."""
        repo_html_v1 = github_trending_repo_html(
            owner="openai", name="codex", stars_in_period="500 stars this week",
        )
        repo_html_v2 = github_trending_repo_html(
            owner="openai", name="codex", stars_in_period="800 stars this week",
        )

        # First run
        _mock_trending_responses(
            mock_http,
            daily_html=github_trending_page(repo_html_v1),
            weekly_html=github_trending_page(repo_html_v1),
            monthly_html=github_trending_page(repo_html_v1),
        )
        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        mock_http.reset()

        # Second run with updated stars
        _mock_trending_responses(
            mock_http,
            daily_html=github_trending_page(repo_html_v2),
            weekly_html=github_trending_page(repo_html_v2),
            monthly_html=github_trending_page(repo_html_v2),
        )
        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            collect(collector, engine, config.github_trending, config.settings)

        discussions = get_discussions(engine, source_type="github_trending")
        weekly = [d for d in discussions if "weekly" in d.external_id]
        assert len(weekly) == 1  # Upserted, not duplicated
        assert weekly[0].score == 800  # Updated


class TestGithubTrendingErrorHandling:
    def test_continues_if_one_period_fails(self, engine, mock_http, collector):
        """If weekly fetch fails, daily and monthly still process."""
        repo_html = github_trending_repo_html()
        page = github_trending_page(repo_html)

        mock_http.get(url=f"{TRENDING_URL}?since=daily").respond(text=page)
        mock_http.get(url=f"{TRENDING_URL}?since=weekly").respond(status_code=500)
        mock_http.get(url=f"{TRENDING_URL}?since=monthly").respond(text=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            count = collect(collector, engine, config.github_trending, config.settings)

        # Only daily + monthly = 2 discussions
        assert count == 2

    def test_empty_page_returns_no_refs(self, engine, mock_http, collector):
        empty_page = "<html><body></body></html>"
        _mock_trending_responses(
            mock_http,
            daily_html=empty_page,
            weekly_html=empty_page,
            monthly_html=empty_page,
        )

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            count = collect(collector, engine, config.github_trending, config.settings)

        assert count == 0


class TestGithubTrendingHelpers:
    """Unit tests for pure helper functions."""

    def test_make_external_id_daily(self):
        from aggre.collectors.github_trending.collector import _make_external_id

        result = _make_external_id("openai", "codex", "daily")
        today = date.today().isoformat()
        assert result == f"openai/codex:daily:{today}"

    def test_make_external_id_weekly(self):
        from aggre.collectors.github_trending.collector import _make_external_id

        result = _make_external_id("openai", "codex", "weekly")
        iso_year, iso_week, _ = date.today().isocalendar()
        assert result == f"openai/codex:weekly:{iso_year}-W{iso_week:02d}"

    def test_make_external_id_monthly(self):
        from aggre.collectors.github_trending.collector import _make_external_id

        result = _make_external_id("openai", "codex", "monthly")
        assert result == f"openai/codex:monthly:{date.today().strftime('%Y-%m')}"

    def test_published_at_daily(self):
        from aggre.collectors.github_trending.collector import _published_at

        result = _published_at("daily")
        assert date.today().isoformat() in result

    def test_published_at_weekly_is_monday(self):
        from aggre.collectors.github_trending.collector import _published_at
        from datetime import timedelta

        result = _published_at("weekly")
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        assert monday.isoformat() in result

    def test_published_at_monthly_is_first(self):
        from aggre.collectors.github_trending.collector import _published_at

        result = _published_at("monthly")
        first = date.today().replace(day=1)
        assert first.isoformat() in result
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/collectors/test_github_trending.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aggre.collectors.github_trending.collector'`

- [ ] **Step 4: Commit test file**

```bash
git add tests/collectors/test_github_trending.py tests/factories.py
git commit -m "test: add GitHub Trending collector tests (red)"
```

### Task 5: Implement the collector

**Files:**
- Create: `src/aggre/collectors/github_trending/collector.py`

- [ ] **Step 1: Implement the collector**

Create `src/aggre/collectors/github_trending/collector.py`:

```python
"""GitHub Trending collector — scrapes github.com/trending."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta

import sqlalchemy as sa

from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.collectors.github_trending.config import GithubTrendingConfig
from aggre.collectors.github_trending.parser import parse_trending_page
from aggre.settings import Settings
from aggre.urls import ensure_content
from aggre.utils.bronze import write_bronze
from aggre.utils.http import create_http_client

logger = logging.getLogger(__name__)

TRENDING_URL = "https://github.com/trending"
PERIODS = ("daily", "weekly", "monthly")

# Weekly/monthly upsert these columns; daily uses on_conflict_do_nothing (append-only)
_UPSERT_COLS = ("score", "published_at", "meta")


class GithubTrendingCollector(BaseCollector):
    """Collect trending repositories from GitHub."""

    source_type = "github_trending"

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: GithubTrendingConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        source_id = self._ensure_source(engine, "GitHub Trending")
        refs: list[DiscussionRef] = []

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for period in PERIODS:
                try:
                    time.sleep(1)
                    url = f"{TRENDING_URL}?since={period}"
                    logger.info("github_trending.fetching period=%s", period)
                    resp = client.get(url)
                    resp.raise_for_status()
                    html = resp.text
                except Exception:
                    logger.exception("github_trending.fetch_failed period=%s", period)
                    continue

                # Store raw HTML snapshot in bronze
                bronze_key = _bronze_key(period)
                write_bronze(self.source_type, bronze_key, "page", html, "html")

                # Parse repos from HTML
                repos = parse_trending_page(html)
                logger.info("github_trending.parsed period=%s repos=%d", period, len(repos))

                for repo in repos:
                    external_id = _make_external_id(repo["owner"], repo["name"], period)
                    raw_data = {**repo, "period": period}
                    refs.append(DiscussionRef(
                        external_id=external_id,
                        raw_data=raw_data,
                        source_id=source_id,
                    ))

        self._update_last_fetched(engine, source_id)
        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        owner = ref_data["owner"]
        name = ref_data["name"]
        period = ref_data["period"]

        repo_url = f"https://github.com/{owner}/{name}"
        content_id = ensure_content(conn, repo_url)

        meta = json.dumps({
            "total_stars": ref_data.get("total_stars", 0),
            "forks": ref_data.get("forks", 0),
            "language": ref_data.get("language", ""),
            "period": period,
        })

        values = dict(
            source_id=source_id,
            source_type=self.source_type,
            external_id=_make_external_id(owner, name, period),
            title=ref_data.get("description", ""),
            author=str(owner),
            url=repo_url,
            published_at=_published_at(period),
            meta=meta,
            content_id=content_id,
            score=ref_data.get("stars_in_period", 0),
        )

        # Daily = append-only (no update columns), weekly/monthly = upsert
        update_columns = _UPSERT_COLS if period != "daily" else None
        self._upsert_discussion(conn, values, update_columns=update_columns)


def _make_external_id(owner: str, name: str, period: str) -> str:
    """Build the external_id for a trending discussion."""
    today = date.today()
    if period == "daily":
        return f"{owner}/{name}:daily:{today.isoformat()}"
    elif period == "weekly":
        iso_year, iso_week, _ = today.isocalendar()
        return f"{owner}/{name}:weekly:{iso_year}-W{iso_week:02d}"
    else:  # monthly
        return f"{owner}/{name}:monthly:{today.strftime('%Y-%m')}"


def _published_at(period: str) -> str:
    """Return the published_at timestamp for a given period."""
    today = date.today()
    if period == "daily":
        dt = today
    elif period == "weekly":
        # Monday of the current ISO week
        dt = today - timedelta(days=today.weekday())
    else:  # monthly
        dt = today.replace(day=1)
    return datetime(dt.year, dt.month, dt.day, tzinfo=UTC).isoformat()


def _bronze_key(period: str) -> str:
    """Build the bronze storage key for a period snapshot."""
    today = date.today()
    if period == "daily":
        return f"daily:{today.isoformat()}"
    elif period == "weekly":
        iso_year, iso_week, _ = today.isocalendar()
        return f"weekly:{iso_year}-W{iso_week:02d}"
    else:
        return f"monthly:{today.strftime('%Y-%m')}"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/collectors/test_github_trending.py -v`
Expected: All tests PASS

- [ ] **Step 3: Check coverage**

Run: `uv run pytest tests/collectors/test_github_trending.py tests/collectors/test_github_trending_parser.py --cov=aggre.collectors.github_trending --cov-report=term-missing`
Expected: High coverage (≥95%) on `collector.py` and `parser.py`

- [ ] **Step 4: Commit**

```bash
git add src/aggre/collectors/github_trending/collector.py
git commit -m "feat: implement GitHub Trending collector"
```

---

## Chunk 4: Registration and Documentation

### Task 6: Register in collection workflow and collector registry

**Files:**
- Modify: `src/aggre/workflows/collection.py`
- Modify: `src/aggre/collectors/__init__.py`

- [ ] **Step 1: Add to collection workflow**

In `src/aggre/workflows/collection.py`, add the import:
```python
from aggre.collectors.github_trending.collector import GithubTrendingCollector
```

Add to `_SOURCES` list:
```python
("github_trending", GithubTrendingCollector, "0 */6 * * *"),
```

- [ ] **Step 2: Add to collector registry**

In `src/aggre/collectors/__init__.py`, add the import:
```python
from aggre.collectors.github_trending.collector import GithubTrendingCollector
```

Add to `COLLECTORS` dict:
```python
"github_trending": GithubTrendingCollector,
```

- [ ] **Step 3: Run full test suite**

Run: `make test-e2e`
Expected: All tests pass (existing + new)

- [ ] **Step 4: Run lint**

Run: `make lint`
Expected: No errors

- [ ] **Step 5: Check diff coverage**

Run: `make coverage-diff`
Expected: ≥95% coverage on changed lines

- [ ] **Step 6: Commit**

```bash
git add src/aggre/workflows/collection.py src/aggre/collectors/__init__.py
git commit -m "feat: register GitHub Trending collector in workflow and registry"
```

### Task 7: Update semantic model documentation

**Files:**
- Modify: `docs/guidelines/semantic-model.md`

- [ ] **Step 1: Update semantic model**

Add `github_trending` to:
1. The `sources.type` values list
2. The `meta` field semantics table: `github_trending` → `{"total_stars": int, "forks": int, "language": str, "period": str}`
3. The `score` semantics table: `github_trending` → Stars gained in period (delta, not total)

- [ ] **Step 2: Commit**

```bash
git add docs/guidelines/semantic-model.md
git commit -m "docs: add github_trending to semantic model"
```
