"""URL enrichment — search HN and Lobsters for discussions about collected URLs."""

from __future__ import annotations

import sqlalchemy as sa
import structlog

from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.config import AppConfig
from aggre.db import SilverContent, _update_content, now_iso

HN_SKIP_DOMAINS = frozenset({
    "youtube.com", "m.youtube.com", "youtu.be",
    "i.redd.it", "v.redd.it",
    "linkedin.com", "www.linkedin.com",
})

LOBSTERS_SKIP_DOMAINS = frozenset({
    "youtube.com", "m.youtube.com", "youtu.be",
    "i.redd.it", "v.redd.it",
    "linkedin.com", "www.linkedin.com",
})


def enrich_content_discussions(
    engine: sa.engine.Engine,
    config: AppConfig,
    log: structlog.stdlib.BoundLogger,
    batch_limit: int = 50,
    *,
    hn_collector: HackernewsCollector,
    lobsters_collector: LobstersCollector,
) -> dict[str, int]:
    """Search HN and Lobsters for discussions about URLs from SilverContent.

    Returns aggregate counts of new discussions found per platform.
    """
    # Find content that hasn't been enriched yet
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverContent.id, SilverContent.canonical_url, SilverContent.domain)
            .where(
                SilverContent.canonical_url.isnot(None),
                SilverContent.enriched_at.is_(None),
            )
            .order_by(SilverContent.created_at.asc())
            .limit(batch_limit)
        ).fetchall()

    if not rows:
        log.info("enrich.no_pending")
        return {"hackernews": 0, "lobsters": 0, "processed": 0}

    log.info("enrich.starting", batch_size=len(rows))

    totals: dict[str, int] = {"hackernews": 0, "lobsters": 0, "processed": 0}

    for row in rows:
        totals["processed"] += 1
        content_url = row.canonical_url
        domain = row.domain
        log.info("enrich.searching", url=content_url)

        failed = False
        skip_hn = domain and domain in HN_SKIP_DOMAINS
        skip_lobsters = domain and domain in LOBSTERS_SKIP_DOMAINS

        if not skip_hn:
            try:
                hn_found = hn_collector.search_by_url(content_url, engine, config.hackernews, config.settings, log)
                totals["hackernews"] += hn_found
            except Exception:
                log.exception("enrich.hn_search_failed", url=content_url)
                failed = True

        if not skip_lobsters:
            try:
                lobsters_found = lobsters_collector.search_by_url(content_url, engine, config.lobsters, config.settings, log)
                totals["lobsters"] += lobsters_found
            except Exception:
                log.exception("enrich.lobsters_search_failed", url=content_url)
                failed = True

        if not failed:
            _update_content(engine, row.id, enriched_at=now_iso())

    log.info("enrich.complete", totals=totals)
    return totals
