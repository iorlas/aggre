"""Transcription sensor -- watches for YouTube content needing transcription.

Note: ``from __future__ import annotations`` is omitted because Dagster's
sensor decorator inspects type hints at decoration time and cannot resolve
deferred (stringified) annotations.
"""

import dagster as dg
import sqlalchemy as sa
from dagster import DagsterRunStatus, RunsFilter

from aggre.dagster_defs.resources import DatabaseResource
from aggre.dagster_defs.transcription.job import transcribe_job
from aggre.db import SilverContent, SilverObservation


@dg.sensor(target=transcribe_job, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.STOPPED)
def transcription_sensor(context: dg.SensorEvaluationContext, database: DatabaseResource) -> dg.SensorResult:
    """Watch for YouTube content needing transcription (text IS NULL, error IS NULL, source_type=youtube)."""
    active_runs = context.instance.get_runs(
        filters=RunsFilter(
            job_name="transcribe_job",
            statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.QUEUED],
        ),
        limit=1,
    )
    if active_runs:
        return dg.SensorResult(skip_reason="transcribe_job already running")

    engine = database.get_engine()
    with engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(SilverContent)
            .join(SilverObservation, SilverObservation.content_id == SilverContent.id)
            .where(
                SilverContent.text.is_(None),
                SilverContent.error.is_(None),
                SilverObservation.source_type == "youtube",
            )
        ).scalar()

    if count and count > 0:
        next_cursor = str(int(context.cursor or "0") + 1)
        return dg.SensorResult(
            run_requests=[dg.RunRequest(run_key=f"transcribe-{next_cursor}")],
            cursor=next_cursor,
        )
    return dg.SensorResult(skip_reason="No videos need transcription")
