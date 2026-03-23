"""Bronze-aware HTTP wrapper — read-through cache for external HTTP calls."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aggre.utils.bronze import (
    DEFAULT_BRONZE_ROOT,
    read_bronze_or_none,
    read_bronze_or_none_by_url,
    write_bronze_by_url,
    write_bronze_json,
)

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

logger = logging.getLogger(__name__)


def fetch_item_json(
    source_type: str,
    external_id: str,
    url: str,
    client: httpx.Client,
    *,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> object:
    """Fetch a JSON API response with item-keyed bronze caching.

    Check bronze → if hit, return cached. If miss → fetch, write bronze, return.
    """
    cached = read_bronze_or_none(source_type, external_id, "raw", "json", bronze_root=bronze_root)
    if cached is not None:
        return json.loads(cached)

    resp = client.get(url)
    resp.raise_for_status()
    data = resp.json()

    write_bronze_json(source_type, external_id, data, bronze_root=bronze_root)
    logger.info("bronze_http.fetched_item source_type=%s external_id=%s", source_type, external_id)
    return data


def fetch_url_text(
    source_type: str,
    url: str,
    client: httpx.Client,
    *,
    artifact_type: str = "response",
    ext: str = "html",
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> str:
    """Fetch a URL with request-keyed bronze caching.

    Check bronze → if hit, return cached. If miss → fetch, write bronze, return.
    """
    cached = read_bronze_or_none_by_url(source_type, url, artifact_type, ext, bronze_root=bronze_root)
    if cached is not None:
        return cached

    resp = client.get(url)
    resp.raise_for_status()
    text = resp.text

    write_bronze_by_url(source_type, url, artifact_type, text, ext, bronze_root=bronze_root)
    logger.info("bronze_http.fetched_url source_type=%s url=%s", source_type, url)
    return text
