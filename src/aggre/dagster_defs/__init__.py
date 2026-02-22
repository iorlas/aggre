"""Dagster definitions for Aggre pipeline."""

from __future__ import annotations

import dagster as dg

from aggre.dagster_defs.jobs.collect import collect_job
from aggre.dagster_defs.jobs.content import content_job
from aggre.dagster_defs.jobs.enrich import enrich_job
from aggre.dagster_defs.jobs.transcribe import transcribe_job
from aggre.dagster_defs.resources import DatabaseResource
from aggre.dagster_defs.sensors import (
    collection_schedule,
    content_sensor,
    enrichment_sensor,
    transcription_sensor,
)

defs = dg.Definitions(
    jobs=[collect_job, content_job, enrich_job, transcribe_job],
    schedules=[collection_schedule],
    sensors=[content_sensor, enrichment_sensor, transcription_sensor],
    resources={
        "database": DatabaseResource(),
    },
)
