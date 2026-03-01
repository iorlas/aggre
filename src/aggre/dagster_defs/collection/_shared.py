"""Shared helpers for per-source collection jobs."""

import logging

import dagster as dg
import sqlalchemy as sa

logger = logging.getLogger(__name__)

_RETRY = dg.RetryPolicy(max_retries=2, delay=10)


def collect_source(engine: sa.engine.Engine, cfg: object, name: str, collector_cls: type) -> int:
    """Collect discussions for one source, process into silver. Returns count."""
    source_config = getattr(cfg, name)
    collector = collector_cls()
    refs = collector.collect_discussions(engine, source_config, cfg.settings)
    logger.info("collect.fetched source=%s discussions=%d", name, len(refs))
    count = 0
    errors = 0
    for ref in refs:
        try:
            with engine.begin() as conn:
                collector.process_discussion(ref["raw_data"], conn, ref["source_id"])
            count += 1
        except Exception:
            logger.exception("collect.process_error source=%s external_id=%s", name, ref["external_id"])
            errors += 1
    logger.info("collect.source_complete source=%s fetched=%d processed=%d errors=%d", name, len(refs), count, errors)
    return count
