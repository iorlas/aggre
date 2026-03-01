"""Comments sensor -- watches for observations needing comments."""

import sqlalchemy as sa

from aggre.dagster_defs.comments.job import comments_job
from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.db import SilverObservation
from aggre.stages.model import StageTracking
from aggre.stages.status import Stage
from aggre.stages.tracking import retry_filter

# Only these source types have comment fetching
_COMMENT_SOURCE_TYPES = ("reddit", "hackernews", "lobsters")

comments_sensor = make_processing_sensor(
    name="comments_sensor",
    target=comments_job,
    query=sa.select(SilverObservation.id)
    .outerjoin(
        StageTracking,
        sa.and_(
            StageTracking.source == SilverObservation.source_type,
            StageTracking.external_id == SilverObservation.external_id,
            StageTracking.stage == Stage.COMMENTS,
        ),
    )
    .where(
        SilverObservation.source_type.in_(_COMMENT_SOURCE_TYPES),
        SilverObservation.comments_json.is_(None),
        sa.or_(
            StageTracking.id.is_(None),
            retry_filter(StageTracking, Stage.COMMENTS),
        ),
    ),
    run_key_prefix="comments",
    skip_message="No observations need comments",
)
