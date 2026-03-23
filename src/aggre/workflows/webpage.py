"""Webpage download and extraction workflow.

Two-task DAG: download → extract. Triggered per-item via "item.new" event.
Hatchet manages concurrency (max 3 per domain) and retry.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging

import httpx
import sqlalchemy as sa
import trafilatura
from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, DefaultFilter

from aggre.config import AppConfig, load_config
from aggre.db import SilverContent, update_content
from aggre.utils.bronze import bronze_exists_by_url, read_bronze_by_url, write_bronze_by_url
from aggre.utils.db import get_engine
from aggre.utils.http import create_http_client
from aggre.workflows.models import SilverContentRef, StepOutput

logger = logging.getLogger(__name__)


SKIP_DOMAINS = frozenset({"youtube.com", "youtu.be", "m.youtube.com", "v.redd.it", "i.redd.it"})
SKIP_EXTENSIONS = (".pdf",)

# Filter: skip domains that aren't webpages (YouTube, image hosts) AND skip items where
# the collector already provided the text (self-posts, Ask HN text, Telegram messages).
# text_provided is a structural signal — it means there's no external page to fetch,
# NOT that processing is complete. See docs/superpowers/specs/2026-03-16-event-dedup-design.md
_skip_domain_expr = "input.domain in [" + ", ".join(f"'{d}'" for d in sorted(SKIP_DOMAINS)) + "]"
_webpage_filter_expr = f"!input.text_provided && !({_skip_domain_expr})"

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
    except Exception:  # noqa: BLE001 — Wayback is best-effort, any failure returns None
        logger.debug("wayback.unavailable url=%s", url)
        return None


def _download_one(
    client: httpx.Client,
    url: str,
    original_url: str | None,
    browserless_url: str = "",
    proxy_url: str = "",
) -> str:
    """Download a single URL and store HTML in bronze.

    Returns status: downloaded/cached/skipped.
    Raises on transient failure (Hatchet handles retry).
    """
    fetch_url = original_url or url

    if any(url.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
        return "skipped"

    # Bronze read-through cache: skip HTTP fetch if already downloaded
    if bronze_exists_by_url("webpage", url, "response", "html"):
        logger.info("webpage_downloader.bronze_hit url=%s", url)
        return "cached"

    try:
        if browserless_url:
            html = _fetch_via_browserless(browserless_url, fetch_url, proxy_url)
        else:
            html = _fetch_direct(client, url, fetch_url)
            if html is None:
                return "skipped"  # 404/410 or non-text content

    except httpx.HTTPStatusError as exc:  # pragma: no cover — HTTP error with Wayback fallback
        logger.warning("webpage_downloader.download_failed url=%s fetch_url=%s status=%d", url, fetch_url, exc.response.status_code)
        html = _fetch_via_wayback(client, url)
        if html is not None:
            write_bronze_by_url("webpage", url, "response", html, "html")
            logger.info("webpage_downloader.wayback_fallback url=%s", url)
            return "downloaded_wayback"
        raise

    except Exception:  # pragma: no cover — unexpected download error with Wayback fallback
        logger.exception("webpage_downloader.download_failed url=%s fetch_url=%s", url, fetch_url)
        html = _fetch_via_wayback(client, url)
        if html is not None:
            write_bronze_by_url("webpage", url, "response", html, "html")
            logger.info("webpage_downloader.wayback_fallback url=%s", url)
            return "downloaded_wayback"
        raise

    write_bronze_by_url("webpage", url, "response", html, "html")
    logger.info("webpage_downloader.downloaded url=%s", url)
    return "downloaded"


_BROWSERLESS_FN = """export default async function ({ page }) {
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    const t = r.resourceType();
    if (["image", "font", "media", "stylesheet"].includes(t)) r.abort();
    else r.continue();
  });
  try {
    const resp = await page.goto(URL, { waitUntil: "networkidle2" });
    return { data: { status: resp.status(), html: await page.content() } };
  } catch (e) {
    return { data: { status: 0, html: "", error: e.message } };
  }
}"""


def _fetch_via_browserless(browserless_url: str, fetch_url: str, proxy_url: str = "") -> str:
    """Render a page via Browserless /chromium/function and return HTML.

    Uses a plain (non-proxied) client to reach the browserless API.
    The proxy is passed to Chromium via --proxy-server launch arg.

    Raises httpx.HTTPStatusError if the target page returns HTTP >= 400.
    """
    code = _BROWSERLESS_FN.replace("URL", json.dumps(fetch_url))
    params: dict[str, str] = {}
    if proxy_url:
        params["launch"] = json.dumps({"args": [f"--proxy-server={proxy_url}"]})
    resp = httpx.post(
        f"{browserless_url}/chromium/function",
        params=params,
        json={"code": code},
        timeout=60.0,
    )
    if resp.status_code >= 400:
        body = resp.text[:500]
        raise httpx.HTTPStatusError(
            f"Browserless error {resp.status_code}: {body}",
            request=resp.request,
            response=resp,
        )

    data = resp.json()["data"]

    if data.get("error"):
        raise httpx.HTTPStatusError(
            f"Navigation failed: {data['error']}",
            request=httpx.Request("POST", fetch_url),
            response=httpx.Response(0, text=data["error"]),
        )

    target_status = data["status"]
    html = data["html"]

    if target_status >= 400:
        raise httpx.HTTPStatusError(
            f"Target returned HTTP {target_status}",
            request=httpx.Request("POST", fetch_url),
            response=httpx.Response(target_status, text=html),
        )

    return html


def _fetch_direct(
    client: httpx.Client,
    url: str,
    fetch_url: str,
) -> str | None:
    """Fetch a page directly via httpx. Returns HTML or None if skipped."""
    resp = client.get(fetch_url)

    # 404/410 — permanently gone, no retry needed
    if resp.status_code in (404, 410):
        logger.warning("webpage_downloader.http_gone url=%s status=%d", url, resp.status_code)
        return None

    resp.raise_for_status()

    # Skip binary content (images, videos, etc.)
    content_type = resp.headers.get("content-type", "")
    if content_type and not _is_text_content_type(content_type):
        logger.info("webpage_downloader.skipped_non_text url=%s content_type=%s", url, content_type)
        return None

    return resp.text


# -- Per-item functions (tested directly) ------------------------------------


def download_one(
    engine: sa.engine.Engine,
    config: AppConfig,
    content_id: int,
) -> StepOutput:
    """Download HTML for a single SilverContent. Returns StepOutput."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(SilverContent.canonical_url, SilverContent.original_url, SilverContent.domain, SilverContent.text).where(
                SilverContent.id == content_id
            )
        ).first()

    if not row:
        return StepOutput(status="skipped", reason="not_found")
    if row.text is not None:
        return StepOutput(status="skipped", reason="already_done", url=row.canonical_url)

    browserless_url = config.settings.browserless_url or ""

    with create_http_client(
        proxy_url=config.settings.proxy_url or None,
        follow_redirects=True,
    ) as client:
        status = _download_one(client, row.canonical_url, row.original_url, browserless_url, config.settings.proxy_url or "")
        return StepOutput(status=status, url=row.canonical_url)


def extract_one(
    engine: sa.engine.Engine,
    content_id: int,
) -> StepOutput:
    """Extract text from downloaded HTML for a single SilverContent. Returns StepOutput."""
    with engine.connect() as conn:
        row = conn.execute(sa.select(SilverContent.canonical_url, SilverContent.text).where(SilverContent.id == content_id)).first()

    if not row:
        return StepOutput(status="skipped", reason="not_found")
    if row.text is not None:
        return StepOutput(status="skipped", reason="already_done", url=row.canonical_url)

    url = row.canonical_url

    try:
        html = read_bronze_by_url("webpage", url, "response", "html")
    except FileNotFoundError:
        return StepOutput(status="skipped", reason="no_bronze", url=url)

    # Extract text with 90s timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(trafilatura.extract, html, include_comments=False, include_tables=False)
        try:
            extracted = future.result(timeout=90)
        except concurrent.futures.TimeoutError:  # pragma: no cover — trafilatura hang safety net
            raise TimeoutError("Content extraction timed out after 90s") from None

    if extracted is None:
        logger.warning("webpage_extractor.no_content url=%s", url)
        return StepOutput(status="no_content", url=url)

    # Extract title from trafilatura metadata
    extracted_title = None
    metadata = trafilatura.metadata.extract_metadata(html)
    if metadata:
        extracted_title = metadata.title

    update_content(engine, content_id, text=extracted, title=extracted_title)
    logger.info("webpage_extractor.extracted url=%s", url)
    return StepOutput(status="extracted", url=url)


# -- Hatchet workflow ----------------------------------------------------------


def register(h):  # pragma: no cover — Hatchet wiring
    """Register the webpage workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-webpage",
        on_events=["item.new"],
        # Two-layer concurrency:
        # 1. GROUP_ROUND_ROBIN by domain — fair scheduling across domains, max 3 per domain
        # 2. CANCEL_NEWEST by content_id — if a run for the same content_id is already
        #    in-flight (queued or running), the new run is immediately cancelled.
        #    This is Layer 2 of event dedup: a safety net for race conditions where
        #    _emit_item_event's check passes but the content gets processed before
        #    the queued task starts. See docs/superpowers/specs/2026-03-16-event-dedup-design.md
        concurrency=[
            ConcurrencyExpression(
                expression="input.domain",
                max_runs=3,
                limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
            ),
            ConcurrencyExpression(
                expression="string(input.content_id)",
                max_runs=1,
                limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
            ),
        ],
        input_validator=SilverContentRef,
        default_filters=[DefaultFilter(expression=_webpage_filter_expr, scope="default")],
    )

    @wf.task(execution_timeout="5m", schedule_timeout="720h", retries=7, backoff_factor=4, backoff_max_seconds=3600)
    def download_task(input: SilverContentRef, ctx) -> StepOutput:
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        result = download_one(engine, cfg, input.content_id)
        ctx.log(f"Download: {result.status} for content_id={input.content_id}")
        return result

    @wf.task(
        parents=[download_task],
        execution_timeout="5m",
        schedule_timeout="720h",
        retries=7,
        backoff_factor=4,
        backoff_max_seconds=3600,
    )
    def extract_task(input: SilverContentRef, ctx) -> StepOutput:
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        result = extract_one(engine, input.content_id)
        ctx.log(f"Extract: {result.status} for content_id={input.content_id}")
        return result

    return wf
