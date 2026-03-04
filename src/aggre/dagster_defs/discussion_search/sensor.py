"""Discussion search sensor -- watches for content ready for cross-source discovery."""

import sqlalchemy as sa

from aggre.dagster_defs.discussion_search.job import discussion_search_job
from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.db import SilverContent
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter
from aggre.tracking.status import Stage

discussion_search_sensor = make_processing_sensor(
    name="discussion_search_sensor",
    target=discussion_search_job,
    query=sa.select(SilverContent.id)
    .outerjoin(
        StageTracking,
        sa.and_(
            StageTracking.source == "webpage",
            StageTracking.external_id == SilverContent.canonical_url,
            StageTracking.stage == Stage.DISCUSSION_SEARCH,
        ),
    )
    .where(
        SilverContent.canonical_url.isnot(None),
        sa.or_(
            StageTracking.id.is_(None),
            retry_filter(StageTracking, Stage.DISCUSSION_SEARCH),
        ),
    ),
    run_key_prefix="discussion_search",
    skip_message="No content needs discussion search",
    minimum_interval_seconds=120,
)
