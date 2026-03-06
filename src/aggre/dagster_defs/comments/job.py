"""Comments job -- fetch comments for discussions that need them.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import logging

import dagster as dg
from dagster import OpExecutionContext, Output

from aggre.collectors import COLLECTORS

logger = logging.getLogger(__name__)

# Sources that support comment fetching
_COMMENT_SOURCES = ("reddit", "hackernews", "lobsters")


@dg.op(required_resource_keys={"database", "app_config"}, retry_policy=dg.RetryPolicy(max_retries=2, delay=10))
def fetch_comments(context: OpExecutionContext) -> Output[int]:
    """Fetch comments for discussions with comments_json=NULL."""
    cfg = context.resources.app_config.get_config()
    engine = context.resources.database.get_engine()

    total = 0
    source_results: dict[str, int] = {}
    error_sources: list[str] = []
    for src_name in _COMMENT_SOURCES:
        cls = COLLECTORS.get(src_name)
        if not cls:
            continue
        collector = cls()
        try:
            count = collector.collect_comments(engine, getattr(cfg, src_name), cfg.settings, batch_limit=10)
            total += count
            source_results[src_name] = count
        except Exception:
            logger.exception("comments.source_error source=%s", src_name)
            error_sources.append(src_name)

    logger.info("comments.complete fetched=%d sources=%s errors=%s", total, source_results, error_sources)
    metadata: dict[str, object] = {"total_fetched": total}
    for src, count in source_results.items():
        metadata[src] = count
    if error_sources:
        metadata["errors"] = ", ".join(error_sources)
    return Output(total, metadata=metadata)


@dg.job(tags={"job_type": "comments"})
def comments_job() -> None:
    fetch_comments()
