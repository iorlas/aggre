"""Collection job -- fetch discussions from configured sources.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors import COLLECTORS
from aggre.config import load_config
from aggre.logging import setup_logging


@dg.op
def collect_all_sources(context: OpExecutionContext) -> int:
    """Collect discussions from all configured sources."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    log = setup_logging(cfg.settings.log_dir, "collect")

    total = 0
    collectors = {name: cls() for name, cls in COLLECTORS.items()}

    for name, collector in collectors.items():
        try:
            source_config = getattr(cfg, name)
            count = collector.collect(engine, source_config, cfg.settings, log)
            total += count
            log.info("collect.source_complete", source=name, new_discussions=count)
        except Exception:
            log.exception("collect.source_error", source=name)

    # Fetch comments
    for src_name in ("reddit", "hackernews", "lobsters"):
        coll = collectors.get(src_name)
        if coll and hasattr(coll, "collect_comments"):
            try:
                coll.collect_comments(engine, getattr(cfg, src_name), cfg.settings, log, batch_limit=10)
            except Exception:
                log.exception("collect.comments_error", source=src_name)

    context.log.info(f"Collected {total} new discussions")
    return total


@dg.job
def collect_job() -> None:
    collect_all_sources()
