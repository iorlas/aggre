"""URL enrichment — search HN and Lobsters for discussions about collected URLs."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa
import structlog

from aggre.collectors.hackernews import HackernewsCollector
from aggre.collectors.lobsters import LobstersCollector
from aggre.config import AppConfig
from aggre.db import SilverPost


def enrich_posts(
    engine: sa.engine.Engine,
    config: AppConfig,
    log: structlog.stdlib.BoundLogger,
    batch_limit: int = 50,
) -> dict[str, int]:
    """Search HN and Lobsters for discussions about URLs from other sources.

    Returns aggregate counts of new discussions found per platform.
    """
    # Find posts with URLs that haven't been enriched yet
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(SilverPost.id, SilverPost.url, SilverPost.meta)
            .where(
                SilverPost.url.isnot(None),
                SilverPost.source_type.notin_(["hackernews", "lobsters"]),
                sa.or_(
                    SilverPost.meta.is_(None),
                    ~SilverPost.meta.like('%"enriched_at"%'),
                ),
            )
            .order_by(SilverPost.published_at.desc())
            .limit(batch_limit)
        ).fetchall()

    if not rows:
        log.info("enrich.no_pending")
        return {"hackernews": 0, "lobsters": 0}

    log.info("enrich.starting", batch_size=len(rows))

    hn_collector = HackernewsCollector()
    lobsters_collector = LobstersCollector()

    totals: dict[str, int] = {"hackernews": 0, "lobsters": 0}

    for row in rows:
        post_url = row.url
        log.info("enrich.searching", url=post_url)

        hn_found = 0
        lobsters_found = 0

        try:
            hn_found = hn_collector.search_by_url(post_url, engine, config, log)
            totals["hackernews"] += hn_found
        except Exception:
            log.exception("enrich.hn_search_failed", url=post_url)

        try:
            lobsters_found = lobsters_collector.search_by_url(post_url, engine, config, log)
            totals["lobsters"] += lobsters_found
        except Exception:
            log.exception("enrich.lobsters_search_failed", url=post_url)

        # Mark post as enriched
        meta = json.loads(row.meta) if row.meta else {}
        meta["enriched_at"] = datetime.now(UTC).isoformat()
        meta["enrichment_results"] = {"hackernews": hn_found, "lobsters": lobsters_found}

        with engine.begin() as conn:
            conn.execute(
                sa.update(SilverPost)
                .where(SilverPost.id == row.id)
                .values(meta=json.dumps(meta))
            )

    log.info("enrich.complete", totals=totals)
    return totals
