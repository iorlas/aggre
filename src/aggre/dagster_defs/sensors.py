"""Dagster sensors and schedules for Aggre pipeline.

Note: ``from __future__ import annotations`` is omitted because Dagster's
sensor/schedule decorators inspect type hints at decoration time and cannot
resolve deferred (stringified) annotations.
"""

import dagster as dg
import sqlalchemy as sa

from aggre.dagster_defs.jobs.collect import collect_job
from aggre.dagster_defs.jobs.content import content_job
from aggre.dagster_defs.jobs.enrich import enrich_job
from aggre.dagster_defs.jobs.transcribe import transcribe_job
from aggre.db import SilverContent, get_engine
from aggre.settings import Settings
from aggre.statuses import FetchStatus, TranscriptionStatus


def _get_engine() -> sa.engine.Engine:
    """Create engine from env vars."""
    settings = Settings()
    return get_engine(settings.database_url)


# -- Schedules -----------------------------------------------------------------

collection_schedule = dg.ScheduleDefinition(
    name="hourly_collection",
    cron_schedule="0 * * * *",
    target=collect_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)


# -- Sensors -------------------------------------------------------------------


@dg.sensor(target=content_job, minimum_interval_seconds=30, default_status=dg.DefaultSensorStatus.STOPPED)
def content_sensor(context: dg.SensorEvaluationContext) -> dg.SensorResult:
    """Watch for pending content downloads."""
    engine = _get_engine()
    with engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(SilverContent)
            .where(SilverContent.fetch_status.in_((FetchStatus.PENDING, FetchStatus.DOWNLOADED)))
        ).scalar()

    if count and count > 0:
        return dg.SensorResult(
            run_requests=[dg.RunRequest(run_key=f"content-{context.cursor or 0}")],
        )
    return dg.SensorResult(skip_reason="No pending content")


@dg.sensor(target=enrich_job, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.STOPPED)
def enrichment_sensor(context: dg.SensorEvaluationContext) -> dg.SensorResult:
    """Watch for unenriched content."""
    engine = _get_engine()
    with engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(SilverContent)
            .where(SilverContent.enriched_at.is_(None), SilverContent.canonical_url.isnot(None))
        ).scalar()

    if count and count > 0:
        return dg.SensorResult(
            run_requests=[dg.RunRequest(run_key=f"enrich-{context.cursor or 0}")],
        )
    return dg.SensorResult(skip_reason="No unenriched content")


@dg.sensor(target=transcribe_job, minimum_interval_seconds=30, default_status=dg.DefaultSensorStatus.STOPPED)
def transcription_sensor(context: dg.SensorEvaluationContext) -> dg.SensorResult:
    """Watch for pending transcriptions."""
    engine = _get_engine()
    with engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count()).select_from(SilverContent).where(SilverContent.transcription_status == TranscriptionStatus.PENDING)
        ).scalar()

    if count and count > 0:
        return dg.SensorResult(
            run_requests=[dg.RunRequest(run_key=f"transcribe-{context.cursor or 0}")],
        )
    return dg.SensorResult(skip_reason="No pending transcriptions")
