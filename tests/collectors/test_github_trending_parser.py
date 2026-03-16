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

    def test_missing_stars_in_period_returns_zero(self):
        html = """
        <article class="Box-row">
          <h2 class="h3 lh-condensed">
            <a href="/owner/repo">
              <span>owner /</span>
              <span class="text-normal">repo</span>
            </a>
          </h2>
          <div class="f6 color-fg-muted mt-2">
          </div>
        </article>
        """
        repos = parse_trending_page(html)
        assert repos[0]["stars_in_period"] == 0

    def test_article_without_link_is_skipped(self):
        html = """
        <article class="Box-row">
          <h2 class="h3 lh-condensed">No link here</h2>
        </article>
        """
        repos = parse_trending_page(html)
        assert repos == []

    def test_article_with_short_href_is_skipped(self):
        html = """
        <article class="Box-row">
          <h2 class="h3 lh-condensed">
            <a href="/onlyone">
              <span class="text-normal">onlyone</span>
            </a>
          </h2>
        </article>
        """
        repos = parse_trending_page(html)
        assert repos == []

    def test_parse_number_returns_zero_for_non_numeric_text(self):
        from aggre.collectors.github_trending.parser import _parse_number

        assert _parse_number("no numbers here") == 0
