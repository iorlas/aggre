"""URL normalization and SilverContent management."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.db import SilverContent

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "source", "campaign", "_ga", "_gid",
})


def normalize_url(url: str) -> str | None:
    """Normalize a URL to a canonical form for deduplication."""
    if not url:
        return None

    url = url.strip()
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return None

    # Force HTTPS
    scheme = "https"

    netloc = parsed.netloc.lower()
    if not netloc:
        return None

    # Remove www.
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path
    query = parsed.query

    # Domain-specific normalization
    if "arxiv.org" in netloc:
        # Strip version suffix from arxiv URLs (e.g., /abs/2301.12345v2 -> /abs/2301.12345)
        path = re.sub(r"v\d+$", "", path)
        query = ""

    elif netloc in ("youtube.com", "m.youtube.com", "youtu.be"):
        is_short = netloc == "youtu.be"
        netloc = "youtube.com"
        params = parse_qs(query)
        if "v" in params:
            path = "/watch"
            query = urlencode({"v": params["v"][0]})
        elif is_short and path:
            video_id = path.strip("/").split("/")[0]
            path = "/watch"
            query = urlencode({"v": video_id})
        else:
            query = ""

    elif "github.com" in netloc:
        # Remove .git suffix
        path = re.sub(r"\.git$", "", path)
        # Remove /tree/branch patterns
        path = re.sub(r"/tree/[^/]+/?$", "", path)
        query = ""

    elif "reddit.com" in netloc:
        netloc = "reddit.com"
        # Normalize to /r/{sub}/comments/{id}
        m = re.match(r"(/r/[^/]+/comments/[^/]+)", path)
        if m:
            path = m.group(1)
        query = ""

    elif "news.ycombinator.com" in netloc:
        params = parse_qs(query)
        if "id" in params:
            query = urlencode({"id": params["id"][0]})
        else:
            query = ""

    elif "medium.com" in netloc or netloc.endswith(".medium.com"):
        params = parse_qs(query)
        params.pop("source", None)
        params.pop("sk", None)
        # Fall through to generic param cleaning below

    else:
        # Generic: remove tracking params, sort remaining
        params = parse_qs(query, keep_blank_values=True)
        cleaned = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
        query = urlencode(sorted(cleaned.items()), doseq=True) if cleaned else ""

    # For domains that already set query above, skip generic cleaning
    if query and "arxiv.org" not in netloc and "youtube.com" not in netloc and \
       "github.com" not in netloc and "reddit.com" not in netloc and \
       "news.ycombinator.com" not in netloc:
        params = parse_qs(query, keep_blank_values=True)
        cleaned = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
        query = urlencode(sorted(cleaned.items()), doseq=True) if cleaned else ""

    # Remove trailing slash
    path = path.rstrip("/") or "/"
    if path == "/":
        path = ""

    # Remove fragment
    result = urlunparse((scheme, netloc, path, "", query, ""))
    return result


def extract_domain(url: str) -> str | None:
    """Extract the domain from a URL, stripping www. prefix."""
    if not url:
        return None
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


def ensure_content(conn: sa.Connection, raw_url: str) -> int | None:
    """Normalize URL, find or create SilverContent, return its id."""
    canonical = normalize_url(raw_url)
    if not canonical:
        return None

    # Try to find existing
    row = conn.execute(
        sa.select(SilverContent.id).where(SilverContent.canonical_url == canonical)
    ).first()
    if row:
        return row[0]

    # Create new with ON CONFLICT DO NOTHING
    domain = extract_domain(canonical)
    stmt = pg_insert(SilverContent).values(canonical_url=canonical, domain=domain)
    stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
    result = conn.execute(stmt)
    if result.rowcount == 0:
        # Race condition: another transaction inserted it
        row = conn.execute(
            sa.select(SilverContent.id).where(SilverContent.canonical_url == canonical)
        ).first()
        return row[0] if row else None
    return result.inserted_primary_key[0]
