"""Bronze-aware HTTP wrapper — read-through cache for external HTTP calls."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import structlog

from aggre.bronze import (
    DEFAULT_BRONZE_ROOT,
    bronze_exists,
    bronze_exists_by_url,
    read_bronze,
    read_bronze_by_url,
    write_bronze_by_url,
    write_bronze_json,
)


def fetch_item_json(
    source_type: str,
    external_id: str,
    url: str,
    client: httpx.Client,
    log: structlog.stdlib.BoundLogger,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> object:
    """Fetch a JSON API response with item-keyed bronze caching.

    Check bronze → if hit, return cached. If miss → fetch, write bronze, return.
    """
    if bronze_exists(source_type, external_id, "raw", "json", bronze_root=bronze_root):
        text = read_bronze(source_type, external_id, "raw", "json", bronze_root=bronze_root)
        return json.loads(text)

    resp = client.get(url)
    resp.raise_for_status()
    data = resp.json()

    write_bronze_json(source_type, external_id, data, bronze_root=bronze_root)
    log.info("bronze_http.fetched_item", source_type=source_type, external_id=external_id)
    return data


def fetch_url_text(
    source_type: str,
    url: str,
    client: httpx.Client,
    log: structlog.stdlib.BoundLogger,
    *,
    artifact_type: str = "response",
    ext: str = "html",
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> str:
    """Fetch a URL with request-keyed bronze caching.

    Check bronze → if hit, return cached. If miss → fetch, write bronze, return.
    """
    if bronze_exists_by_url(source_type, url, artifact_type, ext, bronze_root=bronze_root):
        return read_bronze_by_url(source_type, url, artifact_type, ext, bronze_root=bronze_root)

    resp = client.get(url)
    resp.raise_for_status()
    text = resp.text

    write_bronze_by_url(source_type, url, artifact_type, text, ext, bronze_root=bronze_root)
    log.info("bronze_http.fetched_url", source_type=source_type, url=url)
    return text
