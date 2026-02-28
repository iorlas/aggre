"""Content sensor -- watches for content needing text extraction.

Note: ``from __future__ import annotations`` is omitted because Dagster's
sensor decorator inspects type hints at decoration time and cannot resolve
deferred (stringified) annotations.
"""

import dagster as dg
import sqlalchemy as sa
from dagster import DagsterRunStatus, RunsFilter

from aggre.dagster_defs.content.job import SKIP_DOMAINS, content_job
from aggre.dagster_defs.resources import DatabaseResource
from aggre.db import SilverContent


@dg.sensor(target=content_job, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.STOPPED)
def content_sensor(context: dg.SensorEvaluationContext, database: DatabaseResource) -> dg.SensorResult:
    """Watch for content needing text extraction (text IS NULL, error IS NULL, not YouTube)."""
    active_runs = context.instance.get_runs(
        filters=RunsFilter(
            job_name="content_job",
            statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.QUEUED],
        ),
        limit=1,
    )
    if active_runs:
        return dg.SensorResult(skip_reason="content_job already running")

    engine = database.get_engine()
    with engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(SilverContent)
            .where(
                SilverContent.text.is_(None),
                SilverContent.error.is_(None),
                sa.or_(
                    SilverContent.domain.notin_(SKIP_DOMAINS),
                    SilverContent.domain.is_(None),
                ),
            )
        ).scalar()

    if count and count > 0:
        next_cursor = str(int(context.cursor or "0") + 1)
        return dg.SensorResult(
            run_requests=[dg.RunRequest(run_key=f"content-{next_cursor}")],
            cursor=next_cursor,
        )
    return dg.SensorResult(skip_reason="No content needs processing")
