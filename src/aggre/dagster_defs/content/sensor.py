"""Content sensor -- watches for content needing text extraction."""

import sqlalchemy as sa

from aggre.dagster_defs.content.job import SKIP_DOMAINS, content_job
from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.db import SilverContent
from aggre.stages.model import StageTracking
from aggre.stages.status import Stage, StageStatus
from aggre.stages.tracking import retry_filter

content_sensor = make_processing_sensor(
    name="content_sensor",
    target=content_job,
    query=sa.select(SilverContent.id)
    .outerjoin(
        StageTracking,
        sa.and_(
            StageTracking.source == "content",
            StageTracking.external_id == SilverContent.canonical_url,
            StageTracking.stage == Stage.DOWNLOAD,
        ),
    )
    .where(
        SilverContent.text.is_(None),
        sa.or_(
            SilverContent.domain.notin_(SKIP_DOMAINS),
            SilverContent.domain.is_(None),
        ),
        sa.or_(
            StageTracking.id.is_(None),
            retry_filter(StageTracking, Stage.DOWNLOAD),
        ),
        sa.not_(sa.func.coalesce(StageTracking.status == StageStatus.SKIPPED, False)),
    ),
    run_key_prefix="content",
    skip_message="No content needs processing",
)
