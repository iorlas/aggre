"""Enrichment sensor -- watches for content ready for cross-source discovery."""

import sqlalchemy as sa

from aggre.dagster_defs.enrichment.job import enrich_job
from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.db import SilverContent
from aggre.stages.model import StageTracking
from aggre.stages.status import Stage
from aggre.stages.tracking import retry_filter

enrichment_sensor = make_processing_sensor(
    name="enrichment_sensor",
    target=enrich_job,
    query=sa.select(SilverContent.id)
    .outerjoin(
        StageTracking,
        sa.and_(
            StageTracking.source == "content",
            StageTracking.external_id == SilverContent.canonical_url,
            StageTracking.stage == Stage.ENRICH,
        ),
    )
    .where(
        SilverContent.text.isnot(None),
        SilverContent.canonical_url.isnot(None),
        sa.or_(
            StageTracking.id.is_(None),
            retry_filter(StageTracking, Stage.ENRICH),
        ),
    ),
    run_key_prefix="enrich",
    skip_message="No content needs enrichment",
    minimum_interval_seconds=120,
)
