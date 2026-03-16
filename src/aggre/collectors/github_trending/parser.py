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
