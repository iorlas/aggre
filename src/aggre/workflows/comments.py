"""Comments workflow -- fetch comments for individual discussions.

Triggered per-item via "item.new" event. Self-filters to comment-supporting sources.
Hatchet manages concurrency (max 1 per source) and retry.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy

from aggre.collectors import COLLECTORS
from aggre.config import load_config
from aggre.db import SilverDiscussion
from aggre.settings import Settings
from aggre.utils.db import get_engine
from aggre.workflows.models import ItemEvent

logger = logging.getLogger(__name__)

# Sources that support comment fetching
_COMMENT_SOURCES = ("reddit", "hackernews", "lobsters")


def fetch_one_comments(
    engine: sa.engine.Engine,
    discussion_id: int,
    source: str,
    settings: Settings,
) -> str:
    """Fetch comments for a single discussion. Returns status string."""
    cls = COLLECTORS.get(source)
    if not cls:
        return "no_collector"

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(SilverDiscussion.id, SilverDiscussion.external_id, SilverDiscussion.meta, SilverDiscussion.comments_json).where(
                SilverDiscussion.id == discussion_id
            )
        ).first()

    if not row:
        return "not_found"

    if row.comments_json is not None:
        return "already_done"

    collector = cls()
    collector.fetch_discussion_comments(engine, row.id, row.external_id, row.meta, settings)
    logger.info("comments.fetched source=%s discussion_id=%d external_id=%s", source, discussion_id, row.external_id)
    return "fetched"


# -- Hatchet workflow ----------------------------------------------------------


def register(h):  # pragma: no cover — Hatchet wiring
    """Register the comments workflow with the Hatchet instance."""
    wf = h.workflow(
        name="process-comments",
        on_events=["item.new"],
        concurrency=ConcurrencyExpression(
            expression="input.source",
            max_runs=1,
            limit_strategy=ConcurrencyLimitStrategy.GROUP_ROUND_ROBIN,
        ),
        input_validator=ItemEvent,
    )

    @wf.task(execution_timeout="5m")
    def comments_task(input: ItemEvent, ctx):
        if input.source not in _COMMENT_SOURCES:
            return {"status": "skipped"}
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        status = fetch_one_comments(engine, input.discussion_id, input.source, cfg.settings)
        ctx.log(f"Comments: {status} for discussion_id={input.discussion_id}")
        return {"status": status}

    return wf
