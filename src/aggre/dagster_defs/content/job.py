"""Content download and extraction job.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import concurrent.futures
import logging

import dagster as dg
import httpx
import sqlalchemy as sa
import sqlalchemy.orm
import trafilatura
from dagster import OpExecutionContext

from aggre.config import AppConfig, load_config
from aggre.db import SilverContent, update_content
from aggre.stages.model import StageTracking
from aggre.stages.status import Stage, StageStatus
from aggre.stages.tracking import retry_filter, upsert_done, upsert_failed, upsert_skipped
from aggre.utils.bronze import bronze_exists_by_url, read_bronze_by_url, write_bronze_by_url
from aggre.utils.http import create_http_client

logger = logging.getLogger(__name__)

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


def _download_one(
    client: httpx.Client,
    engine: sa.engine.Engine,
    content_id: int,
    url: str,
    domain: str | None,
) -> int:
    """Download a single URL and store HTML in bronze. Returns 1 on success, 0 on skip."""
    # Pre-request skips: YouTube (saves bandwidth), PDFs
    if domain and domain in SKIP_DOMAINS:
        upsert_skipped(engine, "content", url, Stage.DOWNLOAD, "youtube")
        return 1

    if any(url.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
        upsert_skipped(engine, "content", url, Stage.DOWNLOAD, "pdf")
        return 1

    # Bronze read-through cache: skip HTTP fetch if already downloaded
    if bronze_exists_by_url("content", url, "response", "html"):
        upsert_done(engine, "content", url, Stage.DOWNLOAD)
        logger.info("content_downloader.bronze_hit url=%s", url)
        return 1

    try:
        resp = client.get(url)

        # 404/410 — permanently gone, no traceback needed
        if resp.status_code in (404, 410):
            logger.warning("content_downloader.http_gone url=%s status=%d", url, resp.status_code)
            upsert_failed(engine, "content", url, Stage.DOWNLOAD, f"HTTP {resp.status_code}")
            return 1

        resp.raise_for_status()

        # Skip binary content (images, videos, etc.)
        content_type = resp.headers.get("content-type", "")
        if content_type and not _is_text_content_type(content_type):
            logger.info("content_downloader.skipped_non_text url=%s content_type=%s", url, content_type)
            upsert_skipped(engine, "content", url, Stage.DOWNLOAD, "non_text")
            return 1

        write_bronze_by_url("content", url, "response", resp.text, "html")
        upsert_done(engine, "content", url, Stage.DOWNLOAD)
        logger.info("content_downloader.downloaded url=%s", url)
        return 1

    except httpx.HTTPStatusError as exc:
        logger.warning("content_downloader.download_failed url=%s status=%d", url, exc.response.status_code)
        upsert_failed(engine, "content", url, Stage.DOWNLOAD, str(exc))
        return 1

    except Exception as exc:
        logger.exception("content_downloader.download_failed url=%s", url)
        upsert_failed(engine, "content", url, Stage.DOWNLOAD, str(exc))
        return 1


def download_content(
    engine: sa.engine.Engine,
    config: AppConfig,
    batch_limit: int = 50,
    max_workers: int = 5,
) -> int:
    """Download raw HTML for unprocessed SilverContent rows (parallel HTTP fetches)."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverContent.id, SilverContent.canonical_url, SilverContent.domain)
            .outerjoin(
                StageTracking,
                sa.and_(
                    StageTracking.source == "content",
                    StageTracking.external_id == SilverContent.canonical_url,
                    StageTracking.stage == Stage.DOWNLOAD,
                ),
            )
            .where(
                SilverContent.text.is_(None),
                sa.or_(
                    SilverContent.domain.notin_(SKIP_DOMAINS),
                    SilverContent.domain.is_(None),
                ),
                sa.or_(
                    StageTracking.id.is_(None),
                    retry_filter(StageTracking, Stage.DOWNLOAD),
                ),
                sa.not_(sa.func.coalesce(StageTracking.status == StageStatus.SKIPPED, False)),
            )
            .order_by(SilverContent.created_at.asc())
            .limit(batch_limit)
        ).fetchall()

    if not rows:
        logger.info("content_downloader.no_pending")
        return 0

    logger.info("content_downloader.download_starting batch_size=%d", len(rows))
    processed = 0

    with (
        create_http_client(
            proxy_url=config.settings.proxy_url or None,
            follow_redirects=True,
        ) as client,
        concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor,
    ):
        futures = [executor.submit(_download_one, client, engine, row.id, row.canonical_url, row.domain) for row in rows]
        for future in concurrent.futures.as_completed(futures):
            processed += future.result()

    logger.info("content_downloader.download_complete processed=%d", processed)
    return processed


# -- Extraction ----------------------------------------------------------------


def extract_html_text(
    engine: sa.engine.Engine,
    config: AppConfig,
    batch_limit: int = 50,
) -> int:
    """Extract text from downloaded HTML using trafilatura (single-threaded, CPU-bound)."""
    with engine.connect() as conn:
        # Subquery: content URLs with download done
        download_done_sq = (
            sa.select(StageTracking.external_id)
            .where(
                StageTracking.source == "content",
                StageTracking.stage == Stage.DOWNLOAD,
                StageTracking.status == StageStatus.DONE,
            )
            .subquery()
        )

        # Alias for extract tracking join
        st_extract = sa.orm.aliased(StageTracking)

        rows = conn.execute(
            sa.select(SilverContent.id, SilverContent.canonical_url)
            .where(
                SilverContent.text.is_(None),
                SilverContent.canonical_url.in_(sa.select(download_done_sq.c.external_id)),
            )
            .outerjoin(
                st_extract,
                sa.and_(
                    st_extract.source == "content",
                    st_extract.external_id == SilverContent.canonical_url,
                    st_extract.stage == Stage.EXTRACT,
                ),
            )
            .where(
                sa.or_(
                    st_extract.id.is_(None),
                    retry_filter(st_extract, Stage.EXTRACT),
                ),
            )
            .order_by(SilverContent.created_at.asc())
            .limit(batch_limit)
        ).fetchall()

    if not rows:
        logger.info("content_extractor.no_downloaded")
        return 0

    logger.info("content_extractor.extract_starting batch_size=%d", len(rows))
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

            upsert_done(engine, "content", url, Stage.EXTRACT)
            update_content(engine, content_id, text=result, title=extracted_title)
            processed += 1
            logger.info("content_extractor.extracted url=%s", url)

        except Exception as exc:
            logger.exception("content_extractor.extract_failed url=%s", url)
            upsert_failed(engine, "content", url, Stage.EXTRACT, str(exc))
            processed += 1

    logger.info("content_extractor.extract_complete processed=%d", processed)
    return processed


# -- Dagster ops and job -------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=dg.RetryPolicy(max_retries=2, delay=10))
def download_content_op(context: OpExecutionContext) -> int:
    """Download raw HTML for pending content URLs."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return download_content(engine, cfg)


@dg.op(required_resource_keys={"database"})
def extract_content_op(context: OpExecutionContext, download_count: int) -> int:
    """Extract text from downloaded HTML."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return extract_html_text(engine, cfg)


@dg.job
def content_job() -> None:
    extract_content_op(download_content_op())
