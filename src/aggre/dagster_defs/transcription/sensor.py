"""Transcription sensor — watches for pending transcriptions.

Note: ``from __future__ import annotations`` is omitted because Dagster's
sensor decorator inspects type hints at decoration time and cannot resolve
deferred (stringified) annotations.
"""

import dagster as dg
import sqlalchemy as sa

from aggre.dagster_defs.resources import DatabaseResource
from aggre.dagster_defs.transcription.job import transcribe_job
from aggre.db import SilverContent
from aggre.statuses import TranscriptionStatus


@dg.sensor(target=transcribe_job, minimum_interval_seconds=30, default_status=dg.DefaultSensorStatus.STOPPED)
def transcription_sensor(context: dg.SensorEvaluationContext, database: DatabaseResource) -> dg.SensorResult:
    """Watch for pending transcriptions."""
    engine = database.get_engine()
    with engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count()).select_from(SilverContent).where(SilverContent.transcription_status == TranscriptionStatus.PENDING)
        ).scalar()

    if count and count > 0:
        return dg.SensorResult(
            run_requests=[dg.RunRequest(run_key=f"transcribe-{context.cursor or 0}")],
        )
    return dg.SensorResult(skip_reason="No pending transcriptions")
