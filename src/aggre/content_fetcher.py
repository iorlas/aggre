"""Content fetcher — download and extract article text via trafilatura."""

from __future__ import annotations

import concurrent.futures

import httpx
import sqlalchemy as sa
import structlog
import trafilatura

from aggre.config import AppConfig
from aggre.db import SilverContent, _update_content, now_iso
from aggre.statuses import FetchStatus
from aggre.utils.bronze import bronze_exists_by_url, read_bronze_by_url, write_bronze_by_url
from aggre.utils.http import create_http_client

SKIP_DOMAINS = frozenset({"youtube.com", "youtu.be", "m.youtube.com"})
SKIP_EXTENSIONS = (".pdf",)

TEXT_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "text/plain",
        "application/xhtml+xml",
        "application/xml",
        "text/xml",
    }
)


def _is_text_content_type(content_type: str) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    return mime in TEXT_CONTENT_TYPES


# -- Fetch state transitions --------------------------------------------------


def content_skipped(engine: sa.engine.Engine, content_id: int) -> None:
    """PENDING → SKIPPED (YouTube, PDF, etc.)"""
    _update_content(engine, content_id, fetch_status=FetchStatus.SKIPPED, fetched_at=now_iso())


def content_downloaded(engine: sa.engine.Engine, content_id: int) -> None:
    """PENDING → DOWNLOADED (HTML saved to bronze)"""
    _update_content(engine, content_id, fetch_status=FetchStatus.DOWNLOADED, fetched_at=now_iso())


def content_fetched(engine: sa.engine.Engine, content_id: int, *, body_text: str | None, title: str | None) -> None:
    """DOWNLOADED → FETCHED"""
    _update_content(engine, content_id, body_text=body_text, title=title, fetch_status=FetchStatus.FETCHED, fetched_at=now_iso())


def content_fetch_failed(engine: sa.engine.Engine, content_id: int, *, error: str) -> None:
    """any → FAILED"""
    _update_content(engine, content_id, fetch_status=FetchStatus.FAILED, fetch_error=error, fetched_at=now_iso())


def _download_one(
    client: httpx.Client,
    engine: sa.engine.Engine,
    log: structlog.stdlib.BoundLogger,
    content_id: int,
    url: str,
    domain: str | None,
) -> int:
    """Download a single URL and store HTML in bronze. Returns 1 on success, 0 on skip."""
    # Pre-request skips: YouTube (saves bandwidth), PDFs
    if domain and domain in SKIP_DOMAINS:
        content_skipped(engine, content_id)
        return 1

    if any(url.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
        content_skipped(engine, content_id)
        return 1

    # Bronze read-through cache: skip HTTP fetch if already downloaded
    if bronze_exists_by_url("content", url, "response", "html"):
        content_downloaded(engine, content_id)
        log.info("content_fetcher.bronze_hit", url=url)
        return 1

    try:
        resp = client.get(url)

        # 404/410 — permanently gone, no traceback needed
        if resp.status_code in (404, 410):
            log.warning("content_fetcher.http_gone", url=url, status=resp.status_code)
            content_fetch_failed(engine, content_id, error=f"HTTP {resp.status_code}")
            return 1

        resp.raise_for_status()

        # Skip binary content (images, videos, etc.)
        content_type = resp.headers.get("content-type", "")
        if content_type and not _is_text_content_type(content_type):
            log.info("content_fetcher.skipped_non_text", url=url, content_type=content_type)
            content_skipped(engine, content_id)
            return 1

        write_bronze_by_url("content", url, "response", resp.text, "html")
        content_downloaded(engine, content_id)
        log.info("content_fetcher.downloaded", url=url)
        return 1

    except httpx.HTTPStatusError as exc:
        log.warning("content_fetcher.download_failed", url=url, status=exc.response.status_code)
        content_fetch_failed(engine, content_id, error=str(exc))
        return 1

    except Exception as exc:
        log.exception("content_fetcher.download_failed", url=url)
        content_fetch_failed(engine, content_id, error=str(exc))
        return 1


def download_content(
    engine: sa.engine.Engine,
    config: AppConfig,
    log: structlog.stdlib.BoundLogger,
    batch_limit: int = 50,
    max_workers: int = 5,
) -> int:
    """Download raw HTML for pending SilverContent rows (parallel HTTP fetches)."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverContent.id, SilverContent.canonical_url, SilverContent.domain)
            .where(SilverContent.fetch_status == FetchStatus.PENDING)
            .order_by(SilverContent.created_at.asc())
            .limit(batch_limit)
        ).fetchall()

    if not rows:
        log.info("content_fetcher.no_pending")
        return 0

    log.info("content_fetcher.download_starting", batch_size=len(rows))
    processed = 0
    client = create_http_client(
        proxy_url=config.settings.proxy_url or None,
        follow_redirects=True,
    )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_download_one, client, engine, log, row.id, row.canonical_url, row.domain) for row in rows]
            for future in concurrent.futures.as_completed(futures):
                processed += future.result()
    finally:
        client.close()

    log.info("content_fetcher.download_complete", processed=processed)
    return processed


def extract_html_text(
    engine: sa.engine.Engine,
    config: AppConfig,
    log: structlog.stdlib.BoundLogger,
    batch_limit: int = 50,
) -> int:
    """Extract text from downloaded HTML using trafilatura (single-threaded, CPU-bound)."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverContent.id, SilverContent.canonical_url)
            .where(SilverContent.fetch_status == FetchStatus.DOWNLOADED)
            .order_by(SilverContent.created_at.asc())
            .limit(batch_limit)
        ).fetchall()

    if not rows:
        log.info("content_fetcher.no_downloaded")
        return 0

    log.info("content_fetcher.extract_starting", batch_size=len(rows))
    processed = 0

    for row in rows:
        content_id = row.id
        url = row.canonical_url
        html = read_bronze_by_url("content", url, "response", "html")

        try:
            # Extract text with 90s timeout
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(trafilatura.extract, html, include_comments=False, include_tables=False)
                try:
                    result = future.result(timeout=90)
                except concurrent.futures.TimeoutError:
                    raise TimeoutError("Content extraction timed out after 90s")

            # Extract title from trafilatura metadata
            extracted_title = None
            metadata = trafilatura.metadata.extract_metadata(html)
            if metadata:
                extracted_title = metadata.title

            content_fetched(engine, content_id, body_text=result, title=extracted_title)
            processed += 1
            log.info("content_fetcher.extracted", url=url)

        except Exception as exc:
            log.exception("content_fetcher.extract_failed", url=url)
            content_fetch_failed(engine, content_id, error=str(exc))
            processed += 1

    log.info("content_fetcher.extract_complete", processed=processed)
    return processed
