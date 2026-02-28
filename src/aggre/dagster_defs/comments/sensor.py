"""Comments sensor -- watches for observations needing comments.

Note: ``from __future__ import annotations`` is omitted because Dagster's
sensor decorator inspects type hints at decoration time and cannot resolve
deferred (stringified) annotations.
"""

import dagster as dg
import sqlalchemy as sa
from dagster import DagsterRunStatus, RunsFilter

from aggre.dagster_defs.comments.job import comments_job
from aggre.dagster_defs.resources import DatabaseResource
from aggre.db import SilverObservation

# Only these source types have comment fetching
_COMMENT_SOURCE_TYPES = ("reddit", "hackernews", "lobsters")


@dg.sensor(target=comments_job, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.STOPPED)
def comments_sensor(context: dg.SensorEvaluationContext, database: DatabaseResource) -> dg.SensorResult:
    """Watch for observations with pending comments (comments_json IS NULL AND error IS NULL)."""
    active_runs = context.instance.get_runs(
        filters=RunsFilter(
            job_name="comments_job",
            statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.QUEUED],
        ),
        limit=1,
    )
    if active_runs:
        return dg.SensorResult(skip_reason="comments_job already running")

    engine = database.get_engine()
    with engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(SilverObservation)
            .where(
                SilverObservation.source_type.in_(_COMMENT_SOURCE_TYPES),
                SilverObservation.comments_json.is_(None),
                SilverObservation.error.is_(None),
            )
        ).scalar()

    if count and count > 0:
        next_cursor = str(int(context.cursor or "0") + 1)
        return dg.SensorResult(
            run_requests=[dg.RunRequest(run_key=f"comments-{next_cursor}")],
            cursor=next_cursor,
        )
    return dg.SensorResult(skip_reason="No observations need comments")
