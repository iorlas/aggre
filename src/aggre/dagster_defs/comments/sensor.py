"""Comments sensor -- watches for discussions needing comments."""

import sqlalchemy as sa

from aggre.dagster_defs.comments.job import comments_job
from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.db import SilverDiscussion
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter
from aggre.tracking.status import Stage

# Only these source types have comment fetching
_COMMENT_SOURCE_TYPES = ("reddit", "hackernews", "lobsters")

comments_sensor = make_processing_sensor(
    name="comments_sensor",
    target=comments_job,
    query=sa.select(SilverDiscussion.id)
    .outerjoin(
        StageTracking,
        sa.and_(
            StageTracking.source == SilverDiscussion.source_type,
            StageTracking.external_id == SilverDiscussion.external_id,
            StageTracking.stage == Stage.COMMENTS,
        ),
    )
    .where(
        SilverDiscussion.source_type.in_(_COMMENT_SOURCE_TYPES),
        SilverDiscussion.comments_json.is_(None),
        sa.or_(
            StageTracking.id.is_(None),
            retry_filter(StageTracking, Stage.COMMENTS),
        ),
    ),
    run_key_prefix="comments",
    skip_message="No discussions need comments",
)
