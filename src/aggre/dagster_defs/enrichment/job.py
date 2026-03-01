"""Enrichment job -- discover cross-source discussions.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import logging

import dagster as dg
import sqlalchemy as sa
from dagster import OpExecutionContext

from aggre.collectors.base import SearchableCollector
from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.config import AppConfig, load_config
from aggre.db import SilverContent
from aggre.stages.model import StageTracking
from aggre.stages.status import Stage
from aggre.stages.tracking import retry_filter, upsert_done, upsert_failed

logger = logging.getLogger(__name__)

ENRICHMENT_SKIP_DOMAINS = frozenset(
    {
        "youtube.com",
        "m.youtube.com",
        "youtu.be",
        "i.redd.it",
        "v.redd.it",
        "linkedin.com",
        "www.linkedin.com",
    }
)


def enrich_content_discussions(
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
    # Find content that hasn't been enriched yet
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverContent.id, SilverContent.canonical_url, SilverContent.domain)
            .outerjoin(
                StageTracking,
                sa.and_(
                    StageTracking.source == "content",
                    StageTracking.external_id == SilverContent.canonical_url,
                    StageTracking.stage == Stage.ENRICH,
                ),
            )
            .where(
                SilverContent.text.isnot(None),
                SilverContent.canonical_url.isnot(None),
                sa.or_(
                    StageTracking.id.is_(None),
                    retry_filter(StageTracking, Stage.ENRICH),
                ),
            )
            .order_by(SilverContent.created_at.asc())
            .limit(batch_limit)
        ).fetchall()

    if not rows:
        logger.info("enrich.no_pending")
        return {"hackernews": 0, "lobsters": 0, "processed": 0}

    logger.info("enrich.starting batch_size=%d", len(rows))

    totals: dict[str, int] = {"hackernews": 0, "lobsters": 0, "processed": 0}

    for row in rows:
        totals["processed"] += 1
        content_url = row.canonical_url
        domain = row.domain
        logger.info("enrich.searching url=%s", content_url)

        failed = False
        skip_domain = domain and domain in ENRICHMENT_SKIP_DOMAINS

        if not skip_domain:
            try:
                hn_found = hn_collector.search_by_url(content_url, engine, config.hackernews, config.settings)
                totals["hackernews"] += hn_found
            except Exception:
                logger.exception("enrich.hn_search_failed url=%s", content_url)
                failed = True

        if not skip_domain:
            try:
                lobsters_found = lobsters_collector.search_by_url(content_url, engine, config.lobsters, config.settings)
                totals["lobsters"] += lobsters_found
            except Exception:
                logger.exception("enrich.lobsters_search_failed url=%s", content_url)
                failed = True

        if not failed:
            upsert_done(engine, "content", content_url, Stage.ENRICH)
        else:
            upsert_failed(engine, "content", content_url, Stage.ENRICH, "partial failure")

    logger.info("enrich.complete totals=%s", totals)
    return totals


# -- Dagster ops and job -------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=dg.RetryPolicy(max_retries=2, delay=10))
def enrich_discussions_op(context: OpExecutionContext) -> dict[str, int]:
    """Search HN and Lobsters for discussions about content URLs."""
    cfg = load_config()
    engine = context.resources.database.get_engine()

    return enrich_content_discussions(
        engine,
        cfg,
        hn_collector=HackernewsCollector(),
        lobsters_collector=LobstersCollector(),
    )


@dg.job
def enrich_job() -> None:
    enrich_discussions_op()
