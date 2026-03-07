"""Comments workflow -- fetch comments for discussions that need them."""

from __future__ import annotations

import logging

import sqlalchemy as sa

from aggre.collectors import COLLECTORS
from aggre.config import AppConfig, load_config
from aggre.utils.db import get_engine

logger = logging.getLogger(__name__)

# Sources that support comment fetching
_COMMENT_SOURCES = ("reddit", "hackernews", "lobsters")


def fetch_comments(engine: sa.engine.Engine, config: AppConfig) -> int:
    """Fetch comments for discussions with comments_json=NULL. Returns total count."""
    total = 0
    source_results: dict[str, int] = {}
    error_sources: list[str] = []
    for src_name in _COMMENT_SOURCES:
        cls = COLLECTORS.get(src_name)
        if not cls:
            continue
        collector = cls()
        try:
            count = collector.collect_comments(engine, getattr(config, src_name), config.settings, batch_limit=10)
            total += count
            source_results[src_name] = count
        except Exception:
            logger.exception("comments.source_error source=%s", src_name)
            error_sources.append(src_name)

    logger.info("comments.complete fetched=%d sources=%s errors=%s", total, source_results, error_sources)
    return total


# -- Hatchet workflow ----------------------------------------------------------


def register(h) -> None:  # pragma: no cover — Hatchet wiring
    """Register the comments workflow with the Hatchet instance."""
    wf = h.workflow(name="comments", on_events=["content.new"])

    @wf.task()
    def comments_task(input, ctx):  # noqa: A002
        ctx.log("Starting comment fetching")
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        total = fetch_comments(engine, cfg)
        ctx.log(f"Comments complete: fetched={total}")
        return {"total": total}
