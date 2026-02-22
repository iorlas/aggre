"""Content extractor — extract text from downloaded HTML via trafilatura."""

from __future__ import annotations

import concurrent.futures

import sqlalchemy as sa
import structlog
import trafilatura

from aggre.config import AppConfig
from aggre.db import SilverContent, update_content
from aggre.pipeline.content_downloader import content_fetch_failed
from aggre.statuses import FetchStatus
from aggre.utils.bronze import read_bronze_by_url
from aggre.utils.db import now_iso


def content_fetched(engine: sa.engine.Engine, content_id: int, *, body_text: str | None, title: str | None) -> None:
    """DOWNLOADED → FETCHED"""
    update_content(engine, content_id, body_text=body_text, title=title, fetch_status=FetchStatus.FETCHED, fetched_at=now_iso())


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
        log.info("content_extractor.no_downloaded")
        return 0

    log.info("content_extractor.extract_starting", batch_size=len(rows))
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
            log.info("content_extractor.extracted", url=url)

        except Exception as exc:
            log.exception("content_extractor.extract_failed", url=url)
            content_fetch_failed(engine, content_id, error=str(exc))
            processed += 1

    log.info("content_extractor.extract_complete", processed=processed)
    return processed
