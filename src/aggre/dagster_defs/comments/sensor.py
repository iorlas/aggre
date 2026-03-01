"""Comments sensor -- watches for observations needing comments."""

import sqlalchemy as sa

from aggre.dagster_defs.comments.job import comments_job
from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.db import SilverObservation

# Only these source types have comment fetching
_COMMENT_SOURCE_TYPES = ("reddit", "hackernews", "lobsters")

comments_sensor = make_processing_sensor(
    name="comments_sensor",
    target=comments_job,
    query=sa.select(SilverObservation.id).where(
        SilverObservation.source_type.in_(_COMMENT_SOURCE_TYPES),
        SilverObservation.comments_json.is_(None),
        SilverObservation.error.is_(None),
    ),
    run_key_prefix="comments",
    skip_message="No observations need comments",
)
