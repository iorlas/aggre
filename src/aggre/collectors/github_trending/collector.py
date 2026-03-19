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
                    refs.append(
                        DiscussionRef(
                            external_id=external_id,
                            raw_data=raw_data,
                            source_id=source_id,
                        )
                    )

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

        meta = json.dumps(
            {
                "total_stars": ref_data.get("total_stars", 0),
                "forks": ref_data.get("forks", 0),
                "language": ref_data.get("language", ""),
                "period": period,
            }
        )

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
