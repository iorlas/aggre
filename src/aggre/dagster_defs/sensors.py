"""Sensor factory for processing pipeline sensors.

Note: ``from __future__ import annotations`` is omitted because Dagster's
sensor decorator inspects type hints at decoration time and cannot resolve
deferred (stringified) annotations.
"""

import dagster as dg
import sqlalchemy as sa
from dagster import DagsterRunStatus, RunsFilter

from aggre.dagster_defs.resources import DatabaseResource


def make_processing_sensor(
    *,
    name: str,
    target: dg.JobDefinition,
    query: sa.Select,
    run_key_prefix: str,
    skip_message: str,
    minimum_interval_seconds: int = 60,
) -> dg.SensorDefinition:
    """Create a sensor that triggers a job when a query finds pending rows.

    Includes a concurrency guard: skips if the target job is already running.
    """

    @dg.sensor(name=name, target=target, minimum_interval_seconds=minimum_interval_seconds, default_status=dg.DefaultSensorStatus.RUNNING)
    def _sensor(context: dg.SensorEvaluationContext, database: DatabaseResource) -> dg.SensorResult:
        active = context.instance.get_runs(
            filters=RunsFilter(
                job_name=target.name,
                statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.QUEUED],
            ),
            limit=1,
        )
        if active:
            return dg.SensorResult(skip_reason=f"{target.name} already running")

        engine = database.get_engine()
        with engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(query.subquery())).scalar()

        if count and count > 0:
            next_cursor = str(int(context.cursor or "0") + 1)
            return dg.SensorResult(
                run_requests=[dg.RunRequest(run_key=f"{run_key_prefix}-{next_cursor}")],
                cursor=next_cursor,
            )
        return dg.SensorResult(skip_reason=skip_message)

    return _sensor
