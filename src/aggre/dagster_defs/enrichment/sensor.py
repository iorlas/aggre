"""Enrichment sensor -- watches for content ready for cross-source discovery.

Note: ``from __future__ import annotations`` is omitted because Dagster's
sensor decorator inspects type hints at decoration time and cannot resolve
deferred (stringified) annotations.
"""

import dagster as dg
import sqlalchemy as sa
from dagster import DagsterRunStatus, RunsFilter

from aggre.dagster_defs.enrichment.job import enrich_job
from aggre.dagster_defs.resources import DatabaseResource
from aggre.db import SilverContent


@dg.sensor(target=enrich_job, minimum_interval_seconds=120, default_status=dg.DefaultSensorStatus.STOPPED)
def enrichment_sensor(context: dg.SensorEvaluationContext, database: DatabaseResource) -> dg.SensorResult:
    """Watch for content ready for enrichment (text IS NOT NULL, not recently enriched)."""
    active_runs = context.instance.get_runs(
        filters=RunsFilter(
            job_name="enrich_job",
            statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.QUEUED],
        ),
        limit=1,
    )
    if active_runs:
        return dg.SensorResult(skip_reason="enrich_job already running")

    engine = database.get_engine()
    with engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(SilverContent)
            .where(
                SilverContent.text.isnot(None),
                SilverContent.canonical_url.isnot(None),
                SilverContent.enriched_at.is_(None),
            )
        ).scalar()

    if count and count > 0:
        next_cursor = str(int(context.cursor or "0") + 1)
        return dg.SensorResult(
            run_requests=[dg.RunRequest(run_key=f"enrich-{next_cursor}")],
            cursor=next_cursor,
        )
    return dg.SensorResult(skip_reason="No content needs enrichment")
