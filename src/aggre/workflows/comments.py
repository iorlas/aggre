"""Comments workflow -- fetch comments for individual discussions.

Triggered per-item via "item.new" event. Self-filters to comment-supporting sources.
Hatchet manages concurrency (max 12 per source) and retry.
Uses proxy rotation for Reddit to distribute requests across IPs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import sqlalchemy as sa
from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, DefaultFilter

from aggre.collectors.registry import COLLECTORS
from aggre.config import load_config
from aggre.db import SilverDiscussion
from aggre.utils.db import get_engine
from aggre.utils.proxy_api import get_proxy, report_failure
from aggre.workflows.models import SilverContentRef, StepOutput

if TYPE_CHECKING:
    from aggre.settings import Settings

logger = logging.getLogger(__name__)

# Sources that support comment fetching
_COMMENT_SOURCES = ("reddit", "hackernews", "lobsters")

# Sources that benefit from proxy rotation (IP-based rate limiting)
_PROXY_SOURCES = frozenset({"reddit"})

_comments_filter_expr = "input.source in [" + ", ".join(f"'{s}'" for s in sorted(_COMMENT_SOURCES)) + "]"


def _resolve_proxy(source: str, settings: Settings) -> tuple[str, str]:
    """Resolve proxy for a comment fetch. Returns (proxy_url, proxy_addr).

    Uses proxy API rotation for sources with IP-based rate limiting (Reddit).
    Falls back to static proxy_url for others.
    """
    proxy_api_url = settings.proxy_api_url or ""
    if source in _PROXY_SOURCES and proxy_api_url:
        proxy_info = get_proxy(proxy_api_url, protocol="socks5")
        if proxy_info:
            addr = proxy_info["addr"]
            return f"{proxy_info['protocol']}://{addr}", addr
    return settings.proxy_url or "", ""


def fetch_one_comments(
    engine: sa.engine.Engine,
    discussion_id: int,
    source: str,
    settings: Settings,
) -> StepOutput:
    """Fetch comments for a single discussion. Returns StepOutput."""
    cls = COLLECTORS.get(source)
    if not cls:
        return StepOutput(status="skipped", reason="no_collector")

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(SilverDiscussion.id, SilverDiscussion.external_id, SilverDiscussion.meta, SilverDiscussion.comments_json).where(
                SilverDiscussion.id == discussion_id
            )
        ).first()

    if not row:
        return StepOutput(status="skipped", reason="not_found")

    if row.comments_json is not None:
        return StepOutput(status="skipped", reason="already_done")

    proxy_url, proxy_addr = _resolve_proxy(source, settings)
    proxy_api_url = settings.proxy_api_url or ""

    collector = cls()
    try:
        collector.fetch_discussion_comments(engine, row.id, row.external_id, row.meta, settings, proxy_url=proxy_url or None)
    except Exception:
        if proxy_api_url and proxy_addr:
            report_failure(proxy_api_url, proxy_addr)
        raise
    logger.info("comments.fetched source=%s discussion_id=%d external_id=%s", source, discussion_id, row.external_id)
    return StepOutput(status="fetched")


# -- Hatchet workflow ----------------------------------------------------------


def register(h):  # pragma: no cover — Hatchet wiring
    """Register the comments workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-comments",
        on_events=["item.new"],
        # Two-layer concurrency:
        # 1. GROUP_ROUND_ROBIN by source — fair scheduling across sources, max 12 per source.
        #    Safe with proxy rotation: each worker gets a different IP via proxy API.
        # 2. CANCEL_NEWEST by content_id — dedup safety net, see event-dedup-design.md
        concurrency=[
            ConcurrencyExpression(
                expression="input.source",
                max_runs=12,
                limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
            ),
            ConcurrencyExpression(
                expression="string(input.content_id)",
                max_runs=1,
                limit_strategy=ConcurrencyLimitStrategy.CANCEL_NEWEST,
            ),
        ],
        input_validator=SilverContentRef,
        default_filters=[DefaultFilter(expression=_comments_filter_expr, scope="default")],
    )

    @wf.task(execution_timeout="5m", schedule_timeout="720h", retries=7, backoff_factor=4, backoff_max_seconds=3600)
    def comments_task(input: SilverContentRef, ctx) -> StepOutput:
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        result = fetch_one_comments(engine, input.discussion_id, input.source, cfg.settings)
        ctx.log(f"Comments: {result.status} for discussion_id={input.discussion_id}")
        return result

    return wf
