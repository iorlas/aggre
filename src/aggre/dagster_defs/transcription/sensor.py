"""Transcription sensor -- watches for YouTube content needing transcription."""

import sqlalchemy as sa

from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.dagster_defs.transcription.job import transcribe_job
from aggre.db import SilverContent, SilverObservation
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter
from aggre.tracking.status import Stage

transcription_sensor = make_processing_sensor(
    name="transcription_sensor",
    target=transcribe_job,
    query=sa.select(SilverContent.id)
    .join(SilverObservation, SilverObservation.content_id == SilverContent.id)
    .outerjoin(
        StageTracking,
        sa.and_(
            StageTracking.source == "youtube",
            StageTracking.external_id == SilverObservation.external_id,
            StageTracking.stage == Stage.TRANSCRIBE,
        ),
    )
    .where(
        SilverContent.text.is_(None),
        SilverObservation.source_type == "youtube",
        sa.or_(
            StageTracking.id.is_(None),
            retry_filter(StageTracking, Stage.TRANSCRIBE),
        ),
    ),
    run_key_prefix="transcribe",
    skip_message="No videos need transcription",
)
