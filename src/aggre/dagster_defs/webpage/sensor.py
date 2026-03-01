"""Webpage sensor -- watches for webpages needing text extraction."""

import sqlalchemy as sa

from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.dagster_defs.webpage.job import SKIP_DOMAINS, webpage_job
from aggre.db import SilverContent
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter
from aggre.tracking.status import Stage, StageStatus

webpage_sensor = make_processing_sensor(
    name="webpage_sensor",
    target=webpage_job,
    query=sa.select(SilverContent.id)
    .outerjoin(
        StageTracking,
        sa.and_(
            StageTracking.source == "webpage",
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
    run_key_prefix="webpage",
    skip_message="No webpages need processing",
)
