"""Enrichment sensor — watches for unenriched content.

Note: ``from __future__ import annotations`` is omitted because Dagster's
sensor decorator inspects type hints at decoration time and cannot resolve
deferred (stringified) annotations.
"""

import dagster as dg
import sqlalchemy as sa

from aggre.dagster_defs.enrichment.job import enrich_job
from aggre.dagster_defs.resources import DatabaseResource
from aggre.db import SilverContent


@dg.sensor(target=enrich_job, minimum_interval_seconds=60, default_status=dg.DefaultSensorStatus.STOPPED)
def enrichment_sensor(context: dg.SensorEvaluationContext, database: DatabaseResource) -> dg.SensorResult:
    """Watch for unenriched content."""
    engine = database.get_engine()
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
