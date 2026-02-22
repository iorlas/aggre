"""Enrichment job -- discover cross-source discussions.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.config import load_config
from aggre.pipeline.enrichment import enrich_content_discussions
from aggre.utils.logging import setup_logging


@dg.op
def enrich_discussions_op(context: OpExecutionContext) -> dict[str, int]:
    """Search HN and Lobsters for discussions about content URLs."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    log = setup_logging(cfg.settings.log_dir, "enrich")

    return enrich_content_discussions(
        engine,
        cfg,
        log,
        hn_collector=HackernewsCollector(),
        lobsters_collector=LobstersCollector(),
    )


@dg.job
def enrich_job() -> None:
    enrich_discussions_op()
