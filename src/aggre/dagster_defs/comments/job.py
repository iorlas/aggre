"""Comments job -- fetch comments for observations that need them.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import logging

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors import COLLECTORS
from aggre.config import load_config

logger = logging.getLogger(__name__)

# Sources that support comment fetching
_COMMENT_SOURCES = ("reddit", "hackernews", "lobsters")


@dg.op(required_resource_keys={"database"}, retry_policy=dg.RetryPolicy(max_retries=2, delay=10))
def fetch_comments(context: OpExecutionContext) -> int:
    """Fetch comments for observations with comments_json=NULL."""
    cfg = load_config()
    engine = context.resources.database.get_engine()

    total = 0
    for src_name in _COMMENT_SOURCES:
        cls = COLLECTORS.get(src_name)
        if not cls:
            continue
        collector = cls()
        try:
            count = collector.collect_comments(engine, getattr(cfg, src_name), cfg.settings, batch_limit=10)
            total += count
        except Exception:
            logger.exception("comments.source_error source=%s", src_name)

    context.log.info(f"Fetched comments for {total} observations")
    return total


@dg.job(tags={"job_type": "comments"})
def comments_job() -> None:
    fetch_comments()
