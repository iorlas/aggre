"""Dagster definitions for Aggre pipeline."""

import dagster as dg

from aggre.dagster_defs.collection.job import collect_job
from aggre.dagster_defs.collection.schedule import collection_schedule
from aggre.dagster_defs.content.job import content_job
from aggre.dagster_defs.content.sensor import content_sensor
from aggre.dagster_defs.enrichment.job import enrich_job
from aggre.dagster_defs.enrichment.sensor import enrichment_sensor
from aggre.dagster_defs.resources import DatabaseResource
from aggre.dagster_defs.transcription.job import transcribe_job
from aggre.dagster_defs.transcription.sensor import transcription_sensor

defs = dg.Definitions(
    jobs=[collect_job, content_job, enrich_job, transcribe_job],
    schedules=[collection_schedule],
    sensors=[content_sensor, enrichment_sensor, transcription_sensor],
    resources={
        "database": DatabaseResource(),
    },
)
