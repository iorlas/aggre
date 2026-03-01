"""Transcription sensor -- watches for YouTube content needing transcription."""

import sqlalchemy as sa

from aggre.dagster_defs.sensors import make_processing_sensor
from aggre.dagster_defs.transcription.job import transcribe_job
from aggre.db import SilverContent, SilverObservation

transcription_sensor = make_processing_sensor(
    name="transcription_sensor",
    target=transcribe_job,
    query=sa.select(SilverContent.id)
    .join(SilverObservation, SilverObservation.content_id == SilverContent.id)
    .where(
        SilverContent.text.is_(None),
        SilverContent.error.is_(None),
        SilverObservation.source_type == "youtube",
    ),
    run_key_prefix="transcribe",
    skip_message="No videos need transcription",
)
