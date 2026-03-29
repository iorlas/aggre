"""Tests for GitHub Trending collector."""

from __future__ import annotations

import json
from datetime import date, timedelta
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
            language="Python",
            total_stars="45,231",
            forks="1,234",
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
        repo_html = github_trending_repo_html(owner="openai", name="codex")
        page = github_trending_page(repo_html)
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)
            collect(collector, engine, config.github_trending, config.settings)

        discussions = get_discussions(engine, source_type="github_trending")
        daily = [d for d in discussions if "daily" in d.external_id]
        assert len(daily) == 1

    def test_weekly_upserts_score_and_published_at(self, engine, mock_http, collector):
        repo_html_v1 = github_trending_repo_html(
            owner="openai",
            name="codex",
            stars_in_period="500 stars this week",
        )
        repo_html_v2 = github_trending_repo_html(
            owner="openai",
            name="codex",
            stars_in_period="800 stars this week",
        )

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
        assert len(weekly) == 1
        assert weekly[0].score == 800


class TestGithubTrendingErrorHandling:
    def test_continues_if_one_period_fails(self, engine, mock_http, collector):
        repo_html = github_trending_repo_html()
        page = github_trending_page(repo_html)

        mock_http.get(url=f"{TRENDING_URL}?since=daily").respond(text=page)
        mock_http.get(url=f"{TRENDING_URL}?since=weekly").respond(status_code=500)
        mock_http.get(url=f"{TRENDING_URL}?since=monthly").respond(text=page)

        with patch("aggre.collectors.github_trending.collector.time.sleep"):
            config = make_config()
            count = collect(collector, engine, config.github_trending, config.settings)

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

        result = _published_at("weekly")
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        assert monday.isoformat() in result

    def test_published_at_monthly_is_first(self):
        from aggre.collectors.github_trending.collector import _published_at

        result = _published_at("monthly")
        first = date.today().replace(day=1)
        assert first.isoformat() in result


class TestGithubTrendingProxy:
    def test_collect_calls_get_proxy_once(self, engine, mock_http, collector):
        """collect_discussions() should call get_proxy() once (per-run)."""
        page = github_trending_page(github_trending_repo_html())
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with (
            patch(
                "aggre.collectors.github_trending.collector.get_proxy", return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"}
            ) as mock_gp,
            patch("aggre.collectors.github_trending.collector.time.sleep"),
        ):
            config = make_config(proxy_api_url="http://proxy-hub:8000")
            collect(collector, engine, config.github_trending, config.settings)

        mock_gp.assert_called_once_with("http://proxy-hub:8000", protocol="socks5")

    def test_collect_no_proxy_when_api_url_empty(self, engine, mock_http, collector):
        """collect_discussions() should not call get_proxy() when proxy_api_url is empty."""
        page = github_trending_page(github_trending_repo_html())
        _mock_trending_responses(mock_http, daily_html=page, weekly_html=page, monthly_html=page)

        with (
            patch("aggre.collectors.github_trending.collector.get_proxy") as mock_gp,
            patch("aggre.collectors.github_trending.collector.time.sleep"),
        ):
            config = make_config()
            collect(collector, engine, config.github_trending, config.settings)

        mock_gp.assert_not_called()
