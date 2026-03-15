"""Discussion search workflow -- discover cross-source discussions.

Triggered per-item via "item.new" event. Self-filters to searchable domains.
Hatchet manages concurrency (max 1 search at a time) and retry.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, DefaultFilter

from aggre.collectors.base import SearchableCollector
from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.config import AppConfig, load_config
from aggre.db import SilverContent, update_content
from aggre.utils.db import get_engine, now_iso
from aggre.workflows.models import ItemEvent, StepOutput

logger = logging.getLogger(__name__)

DISCUSSION_SEARCH_SKIP_DOMAINS = frozenset(
    {
        "youtube.com",
        "m.youtube.com",
        "youtu.be",
        "reddit.com",
        "old.reddit.com",
        "i.redd.it",
        "v.redd.it",
        "linkedin.com",
    }
)

_search_filter_expr = "!(" + "input.domain in [" + ", ".join(f"'{d}'" for d in sorted(DISCUSSION_SEARCH_SKIP_DOMAINS)) + "])"


def search_one(
    engine: sa.engine.Engine,
    config: AppConfig,
    content_id: int,
    *,
    hn_collector: SearchableCollector | None = None,
    lobsters_collector: SearchableCollector | None = None,
) -> StepOutput:
    """Search HN and Lobsters for discussions about a single content URL.

    Returns StepOutput. Raises if both searches fail.
    """
    with engine.connect() as conn:
        row = conn.execute(sa.select(SilverContent.canonical_url).where(SilverContent.id == content_id)).first()

    if not row or not row.canonical_url:
        return StepOutput(status="skipped", reason="not_found")

    content_url = row.canonical_url

    if hn_collector is None:
        hn_collector = HackernewsCollector()
    if lobsters_collector is None:
        lobsters_collector = LobstersCollector()

    hn_found = 0
    lobsters_found = 0
    hn_error = None
    lobsters_error = None

    try:
        hn_found = hn_collector.search_by_url(content_url, engine, config.hackernews, config.settings)
    except Exception as e:
        logger.exception("discussion_search.hn_search_failed url=%s", content_url)
        hn_error = e

    try:
        lobsters_found = lobsters_collector.search_by_url(content_url, engine, config.lobsters, config.settings)
    except Exception as e:  # pragma: no cover — external API error
        logger.exception("discussion_search.lobsters_search_failed url=%s", content_url)
        lobsters_error = e

    # If both failed, raise so Hatchet retries
    if hn_error and lobsters_error:
        raise hn_error  # pragma: no cover — both APIs down

    detail: dict[str, str] = {"hackernews": str(hn_found), "lobsters": str(lobsters_found)}
    if hn_error:
        detail["hackernews_error"] = str(hn_error)
    if lobsters_error:
        detail["lobsters_error"] = str(lobsters_error)

    status = "searched_partial" if (hn_error or lobsters_error) else "searched"
    logger.info("discussion_search.searched url=%s status=%s hackernews=%d lobsters=%d", content_url, status, hn_found, lobsters_found)
    update_content(engine, content_id, discussions_searched_at=now_iso())
    return StepOutput(status=status, url=content_url, detail=detail)


# -- Hatchet workflow ----------------------------------------------------------


def register(h):  # pragma: no cover — Hatchet wiring
    """Register the discussion search workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-discussion-search",
        on_events=["item.new"],
        concurrency=ConcurrencyExpression(
            expression="'search'",
            max_runs=1,
            limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
        ),
        input_validator=ItemEvent,
        default_filters=[DefaultFilter(expression=_search_filter_expr, scope="default")],
    )

    @wf.task(execution_timeout="5m", schedule_timeout="720h", retries=7, backoff_factor=4, backoff_max_seconds=3600)
    def discussion_search_task(input: ItemEvent, ctx) -> StepOutput:
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        result = search_one(engine, cfg, input.content_id)
        ctx.log(f"Discussion search: {result.status} for content_id={input.content_id}")
        return result

    return wf
