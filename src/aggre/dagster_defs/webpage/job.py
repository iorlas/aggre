"""Webpage download and extraction job.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import concurrent.futures
import logging
import traceback

import dagster as dg
import httpx
import sqlalchemy as sa
import sqlalchemy.orm
import trafilatura
from dagster import OpExecutionContext

from aggre.config import AppConfig
from aggre.db import SilverContent, update_content
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter, upsert_done, upsert_failed, upsert_skipped
from aggre.tracking.status import Stage, StageStatus
from aggre.utils.bronze import bronze_exists_by_url, read_bronze_by_url, write_bronze_by_url
from aggre.utils.http import create_http_client

logger = logging.getLogger(__name__)


class TargetHTTPError(Exception):
    def __init__(self, status_code: int, url: str, body: str = "") -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code} from target {url}")


_BROWSERLESS_JS = """\
export default async ({ page, context }) => {
  const blocked = new Set(["image", "font", "media", "stylesheet"]);
  await page.setRequestInterception(true);
  page.on("request", req => {
    if (blocked.has(req.resourceType())) req.abort();
    else req.continue();
  });
  const resp = await page.goto(context.url, {
    waitUntil: "networkidle0",
    timeout: 55000,
  });
  const html = await page.content();
  return {
    data: { status: resp ? resp.status() : 0, html },
    type: "application/json",
  };
};
"""

SKIP_DOMAINS = frozenset({"youtube.com", "youtu.be", "m.youtube.com", "v.redd.it", "i.redd.it"})
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


WAYBACK_API = "https://archive.org/wayback/available"


def _fetch_via_wayback(client: httpx.Client, url: str) -> str | None:  # pragma: no cover — Wayback Machine fallback
    """Try fetching a page from the Wayback Machine. Returns HTML or None."""
    try:
        resp = client.get(WAYBACK_API, params={"url": url}, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        snapshot = data.get("archived_snapshots", {}).get("closest", {})
        if not snapshot.get("available"):
            return None
        archive_url = snapshot["url"]
        html_resp = client.get(archive_url, timeout=30.0)
        html_resp.raise_for_status()
        return html_resp.text
    except Exception:
        logger.debug("wayback.unavailable url=%s", url)
        return None


def _download_one(
    client: httpx.Client,
    engine: sa.engine.Engine,
    content_id: int,
    url: str,
    original_url: str | None,
    domain: str | None,
    browserless_url: str = "",
) -> str:
    """Download a single URL and store HTML in bronze. Returns status: downloaded/cached/failed/skipped."""
    fetch_url = original_url or url

    try:
        if any(url.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
            upsert_skipped(engine, "webpage", url, Stage.DOWNLOAD, "pdf")
            return "skipped"

        # Bronze read-through cache: skip HTTP fetch if already downloaded
        if bronze_exists_by_url("webpage", url, "response", "html"):
            upsert_done(engine, "webpage", url, Stage.DOWNLOAD)
            logger.info("webpage_downloader.bronze_hit url=%s", url)
            return "cached"

        if browserless_url:
            html = _fetch_via_browserless(client, browserless_url, fetch_url)
        else:
            html = _fetch_direct(client, engine, url, fetch_url)
            if html is None:
                return "skipped"  # Already tracked (404/410/non-text)

        write_bronze_by_url("webpage", url, "response", html, "html")
        upsert_done(engine, "webpage", url, Stage.DOWNLOAD)
        logger.info("webpage_downloader.downloaded url=%s", url)
        return "downloaded"

    except TargetHTTPError as exc:
        logger.warning("webpage_downloader.target_http_error url=%s fetch_url=%s status=%d", url, fetch_url, exc.status_code)
        # Don't try Wayback for 404/410 (content genuinely gone)
        if exc.status_code not in (404, 410):  # pragma: no cover — Wayback fallback on HTTP error
            html = _fetch_via_wayback(client, url)
            if html is not None:
                write_bronze_by_url("webpage", url, "response", html, "html")
                upsert_done(engine, "webpage", url, Stage.DOWNLOAD)
                logger.info("webpage_downloader.wayback_fallback url=%s", url)
                return "downloaded"
        error_detail = f"HTTP {exc.status_code}"
        if exc.body:
            error_detail += f"\n{exc.body[:2000]}"
        upsert_failed(engine, "webpage", url, Stage.DOWNLOAD, error_detail)
        return "failed"

    except httpx.HTTPStatusError as exc:  # pragma: no cover — HTTP error with Wayback fallback
        logger.warning("webpage_downloader.download_failed url=%s fetch_url=%s status=%d", url, fetch_url, exc.response.status_code)
        html = _fetch_via_wayback(client, url)
        if html is not None:
            write_bronze_by_url("webpage", url, "response", html, "html")
            upsert_done(engine, "webpage", url, Stage.DOWNLOAD)
            logger.info("webpage_downloader.wayback_fallback url=%s", url)
            return "downloaded"
        error_detail = str(exc)
        try:
            body = exc.response.text
            if body:
                error_detail += f"\n{body[:2000]}"
        except Exception:
            pass
        upsert_failed(engine, "webpage", url, Stage.DOWNLOAD, error_detail)
        return "failed"

    except Exception:  # pragma: no cover — unexpected download error with Wayback fallback
        logger.exception("webpage_downloader.download_failed url=%s fetch_url=%s", url, fetch_url)
        html = _fetch_via_wayback(client, url)
        if html is not None:
            write_bronze_by_url("webpage", url, "response", html, "html")
            upsert_done(engine, "webpage", url, Stage.DOWNLOAD)
            logger.info("webpage_downloader.wayback_fallback url=%s", url)
            return "downloaded"
        upsert_failed(engine, "webpage", url, Stage.DOWNLOAD, traceback.format_exc())
        return "failed"


def _fetch_via_browserless(  # pragma: no cover — external browserless service
    client: httpx.Client, browserless_url: str, fetch_url: str
) -> str:
    """Render a page via browserless /function endpoint and return the HTML.

    Raises TargetHTTPError when the target page returns status >= 400.
    """
    resp = client.post(
        f"{browserless_url}/function",
        json={
            "code": _BROWSERLESS_JS,
            "context": {"url": fetch_url},
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    status = data["data"]["status"]
    if status >= 400:
        raise TargetHTTPError(status, fetch_url, data["data"]["html"])
    return data["data"]["html"]


def _fetch_direct(
    client: httpx.Client,
    engine: sa.engine.Engine,
    url: str,
    fetch_url: str,
) -> str | None:
    """Fetch a page directly via httpx. Returns HTML or None if skipped/failed."""
    resp = client.get(fetch_url)

    # 404/410 — permanently gone, no traceback needed
    if resp.status_code in (404, 410):
        logger.warning("webpage_downloader.http_gone url=%s status=%d", url, resp.status_code)
        upsert_failed(engine, "webpage", url, Stage.DOWNLOAD, f"HTTP {resp.status_code}")
        return None

    resp.raise_for_status()

    # Skip binary content (images, videos, etc.)
    content_type = resp.headers.get("content-type", "")
    if content_type and not _is_text_content_type(content_type):
        logger.info("webpage_downloader.skipped_non_text url=%s content_type=%s", url, content_type)
        upsert_skipped(engine, "webpage", url, Stage.DOWNLOAD, "non_text")
        return None

    return resp.text


def download_content(
    engine: sa.engine.Engine,
    config: AppConfig,
    batch_limit: int = 50,
    max_workers: int = 5,
) -> int:
    """Download raw HTML for unprocessed SilverContent rows (parallel HTTP fetches)."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverContent.id, SilverContent.canonical_url, SilverContent.original_url, SilverContent.domain)
            .outerjoin(
                StageTracking,
                sa.and_(
                    StageTracking.source == "webpage",
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
        logger.info("webpage_downloader.no_pending")
        return 0

    browserless_url = config.settings.browserless_url or ""
    logger.info("webpage_downloader.download_starting batch_size=%d browserless=%s", len(rows), bool(browserless_url))
    counts: dict[str, int] = {"downloaded": 0, "cached": 0, "failed": 0, "skipped": 0}

    with (
        create_http_client(
            proxy_url=config.settings.proxy_url or None,
            follow_redirects=True,
        ) as client,
        concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor,
    ):
        future_to_url = {
            executor.submit(
                _download_one, client, engine, row.id, row.canonical_url, row.original_url, row.domain, browserless_url
            ): row.canonical_url
            for row in rows
        }
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                status = future.result()
            except Exception:  # pragma: no cover — worker thread crash
                logger.exception("webpage_downloader.worker_exception url=%s", url)
                upsert_failed(engine, "webpage", url, Stage.DOWNLOAD, traceback.format_exc())
                status = "failed"
            counts[status] = counts.get(status, 0) + 1

    logger.info(
        "webpage_downloader.download_complete downloaded=%d cached=%d failed=%d skipped=%d",
        counts["downloaded"],
        counts["cached"],
        counts["failed"],
        counts["skipped"],
    )
    return counts["downloaded"] + counts["cached"] + counts["failed"] + counts["skipped"]


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
                StageTracking.source == "webpage",
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
                    st_extract.source == "webpage",
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
        logger.info("webpage_extractor.no_downloaded")
        return 0

    logger.info("webpage_extractor.extract_starting batch_size=%d", len(rows))
    extracted = 0
    failed = 0

    for row in rows:
        content_id = row.id
        url = row.canonical_url

        try:
            html = read_bronze_by_url("webpage", url, "response", "html")
            # Extract text with 90s timeout
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(trafilatura.extract, html, include_comments=False, include_tables=False)
                try:
                    result = future.result(timeout=90)
                except concurrent.futures.TimeoutError:  # pragma: no cover — trafilatura hang safety net
                    raise TimeoutError("Content extraction timed out after 90s")

            if result is None:
                upsert_failed(engine, "webpage", url, Stage.EXTRACT, "no_extractable_content")
                failed += 1
                logger.warning("webpage_extractor.no_content url=%s", url)
                continue

            # Extract title from trafilatura metadata
            extracted_title = None
            metadata = trafilatura.metadata.extract_metadata(html)
            if metadata:
                extracted_title = metadata.title

            upsert_done(engine, "webpage", url, Stage.EXTRACT)
            update_content(engine, content_id, text=result, title=extracted_title)
            extracted += 1
            logger.info("webpage_extractor.extracted url=%s", url)

        except Exception as exc:
            logger.exception("webpage_extractor.extract_failed url=%s", url)
            upsert_failed(engine, "webpage", url, Stage.EXTRACT, str(exc))
            failed += 1

    logger.info("webpage_extractor.extract_complete extracted=%d failed=%d", extracted, failed)
    return extracted + failed


# -- Dagster ops and job -------------------------------------------------------


@dg.op(required_resource_keys={"database", "app_config"}, retry_policy=dg.RetryPolicy(max_retries=2, delay=10))
def download_webpage_op(context: OpExecutionContext) -> int:  # pragma: no cover — Dagster op wiring
    """Download raw HTML for pending content URLs."""
    cfg = context.resources.app_config.get_config()
    engine = context.resources.database.get_engine()
    return download_content(engine, cfg)


@dg.op(required_resource_keys={"database", "app_config"})
def extract_webpage_op(context: OpExecutionContext, download_count: int) -> int:  # pragma: no cover — Dagster op wiring
    """Extract text from downloaded HTML."""
    cfg = context.resources.app_config.get_config()
    engine = context.resources.database.get_engine()
    return extract_html_text(engine, cfg)


@dg.job
def webpage_job() -> None:
    extract_webpage_op(download_webpage_op())
