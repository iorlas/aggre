"""Discussion search workflow -- discover cross-source discussions."""

from __future__ import annotations

import logging

import sqlalchemy as sa

from aggre.collectors.base import SearchableCollector
from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.config import AppConfig, load_config
from aggre.db import SilverContent
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter, upsert_done, upsert_failed
from aggre.tracking.status import Stage
from aggre.utils.db import get_engine

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


def search_content_discussions(
    engine: sa.engine.Engine,
    config: AppConfig,
    batch_limit: int = 50,
    *,
    hn_collector: SearchableCollector,
    lobsters_collector: SearchableCollector,
) -> dict[str, int]:
    """Search HN and Lobsters for discussions about URLs from SilverContent.

    Returns aggregate counts of new discussions found per platform.
    """
    # Find content that hasn't been searched yet
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverContent.id, SilverContent.canonical_url)
            .outerjoin(
                StageTracking,
                sa.and_(
                    StageTracking.source == "webpage",
                    StageTracking.external_id == SilverContent.canonical_url,
                    StageTracking.stage == Stage.DISCUSSION_SEARCH,
                ),
            )
            .where(
                SilverContent.canonical_url.isnot(None),
                SilverContent.domain.notin_(DISCUSSION_SEARCH_SKIP_DOMAINS),
                sa.or_(
                    StageTracking.id.is_(None),
                    retry_filter(StageTracking, Stage.DISCUSSION_SEARCH),
                ),
            )
            .order_by(SilverContent.created_at.asc())
            .limit(batch_limit)
        ).fetchall()

    if not rows:
        logger.info("discussion_search.no_pending")
        return {"hackernews": 0, "lobsters": 0, "processed": 0}

    logger.info("discussion_search.starting batch_size=%d", len(rows))

    totals: dict[str, int] = {"hackernews": 0, "lobsters": 0, "processed": 0}

    for row in rows:
        content_url = row.canonical_url
        try:
            logger.info("discussion_search.searching url=%s", content_url)

            failed = False
            hn_found = 0
            lobsters_found = 0

            try:
                hn_found = hn_collector.search_by_url(content_url, engine, config.hackernews, config.settings)
                totals["hackernews"] += hn_found
            except Exception:
                logger.exception("discussion_search.hn_search_failed url=%s", content_url)
                failed = True

            try:
                lobsters_found = lobsters_collector.search_by_url(content_url, engine, config.lobsters, config.settings)
                totals["lobsters"] += lobsters_found
            except Exception:  # pragma: no cover — external API error
                logger.exception("discussion_search.lobsters_search_failed url=%s", content_url)
                failed = True

            logger.info("discussion_search.searched url=%s hackernews=%d lobsters=%d", content_url, hn_found, lobsters_found)

            if not failed:
                upsert_done(engine, "webpage", content_url, Stage.DISCUSSION_SEARCH)
            else:
                upsert_failed(engine, "webpage", content_url, Stage.DISCUSSION_SEARCH, "partial failure")
        except Exception:  # pragma: no cover — unexpected item-level failure
            logger.exception("discussion_search.item_failed url=%s", content_url)
            try:
                upsert_failed(engine, "webpage", content_url, Stage.DISCUSSION_SEARCH, "item processing error")
            except Exception:
                pass  # DB is down — logged above
        totals["processed"] += 1

    logger.info("discussion_search.complete totals=%s", totals)
    return totals


# -- Hatchet workflow ----------------------------------------------------------


def register(h) -> None:  # pragma: no cover — Hatchet wiring
    """Register the discussion search workflow with the Hatchet instance."""
    wf = h.workflow(name="discussion-search", on_events=["content.new"])

    @wf.task()
    def discussion_search_task(input, ctx):  # noqa: A002
        ctx.log("Starting discussion search")
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        stats = search_content_discussions(
            engine,
            cfg,
            hn_collector=HackernewsCollector(),
            lobsters_collector=LobstersCollector(),
        )
        ctx.log(f"Discussion search complete: {stats}")
        return stats
