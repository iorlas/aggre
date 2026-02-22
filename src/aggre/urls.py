"""URL normalization and SilverContent management."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.db import SilverContent
from aggre.utils.urls import extract_domain, strip_tracking_params

# Domain-specific normalizers that fully own query handling (skip generic cleaning)
_DOMAIN_OWNED_QUERY = frozenset({"arxiv.org", "youtube.com", "github.com", "reddit.com", "news.ycombinator.com"})


def normalize_url(url: str | None) -> str | None:
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
        path = re.sub(r"\.git$", "", path)
        path = re.sub(r"/tree/[^/]+/?$", "", path)
        query = ""

    elif "reddit.com" in netloc:
        netloc = "reddit.com"
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
        query = strip_tracking_params(urlencode(params, doseq=True) if params else "")

    else:
        query = strip_tracking_params(query)

    # Generic tracking-param cleanup for domains that don't fully own query handling
    if query and not any(d in netloc for d in _DOMAIN_OWNED_QUERY):
        query = strip_tracking_params(query)

    # Remove trailing slash
    path = path.rstrip("/") or "/"
    if path == "/":
        path = ""

    # Remove fragment
    return urlunparse((scheme, netloc, path, "", query, ""))


def ensure_content(conn: sa.Connection, raw_url: str) -> int | None:
    """Normalize URL, find or create SilverContent, return its id."""
    canonical = normalize_url(raw_url)
    if not canonical:
        return None

    # Try to find existing
    row = conn.execute(sa.select(SilverContent.id).where(SilverContent.canonical_url == canonical)).first()
    if row:
        return row[0]

    # Create new with ON CONFLICT DO NOTHING
    domain = extract_domain(canonical)
    stmt = pg_insert(SilverContent).values(canonical_url=canonical, domain=domain)
    stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
    result = conn.execute(stmt)
    if result.rowcount == 0:
        # Race condition: another transaction inserted it
        row = conn.execute(sa.select(SilverContent.id).where(SilverContent.canonical_url == canonical)).first()
        return row[0] if row else None
    return result.inserted_primary_key[0]
