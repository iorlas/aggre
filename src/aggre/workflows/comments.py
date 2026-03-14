"""Comments workflow -- fetch comments for individual discussions.

Triggered per-item via "item.new" event. Self-filters to comment-supporting sources.
Hatchet manages concurrency (max 1 per source) and retry.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from hatchet_sdk import ConcurrencyExpression, ConcurrencyLimitStrategy, DefaultFilter

from aggre.collectors import COLLECTORS
from aggre.config import load_config
from aggre.db import SilverDiscussion
from aggre.settings import Settings
from aggre.utils.db import get_engine
from aggre.workflows.models import ItemEvent, StepOutput

logger = logging.getLogger(__name__)

# Sources that support comment fetching
_COMMENT_SOURCES = ("reddit", "hackernews", "lobsters")

_comments_filter_expr = "input.source in [" + ", ".join(f"'{s}'" for s in sorted(_COMMENT_SOURCES)) + "]"


def fetch_one_comments(
    engine: sa.engine.Engine,
    discussion_id: int,
    source: str,
    settings: Settings,
) -> StepOutput:
    """Fetch comments for a single discussion. Returns StepOutput."""
    cls = COLLECTORS.get(source)
    if not cls:
        return StepOutput(status="skipped", reason="no_collector")

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(SilverDiscussion.id, SilverDiscussion.external_id, SilverDiscussion.meta, SilverDiscussion.comments_json).where(
                SilverDiscussion.id == discussion_id
            )
        ).first()

    if not row:
        return StepOutput(status="skipped", reason="not_found")

    if row.comments_json is not None:
        return StepOutput(status="skipped", reason="already_done")

    collector = cls()
    collector.fetch_discussion_comments(engine, row.id, row.external_id, row.meta, settings)
    logger.info("comments.fetched source=%s discussion_id=%d external_id=%s", source, discussion_id, row.external_id)
    return StepOutput(status="fetched")


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
        default_filters=[DefaultFilter(expression=_comments_filter_expr, scope="default")],
    )

    @wf.task(execution_timeout="5m", schedule_timeout="720h", retries=7, backoff_factor=4, backoff_max_seconds=3600)
    def comments_task(input: ItemEvent, ctx):
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        result = fetch_one_comments(engine, input.discussion_id, input.source, cfg.settings)
        ctx.log(f"Comments: {result.status} for discussion_id={input.discussion_id}")
        return result.model_dump(exclude_none=True)

    return wf
