"""Dagster definitions for Aggre pipeline."""

import dagster as dg

from aggre.dagster_defs.collection.job import collect_job
from aggre.dagster_defs.collection.schedule import collection_schedule
from aggre.dagster_defs.comments.job import comments_job
from aggre.dagster_defs.comments.sensor import comments_sensor
from aggre.dagster_defs.content.job import content_job
from aggre.dagster_defs.content.sensor import content_sensor
from aggre.dagster_defs.enrichment.job import enrich_job
from aggre.dagster_defs.enrichment.sensor import enrichment_sensor
from aggre.dagster_defs.reprocess.job import reprocess_job
from aggre.dagster_defs.resources import AppConfigResource, DatabaseResource
from aggre.dagster_defs.transcription.job import transcribe_job
from aggre.dagster_defs.transcription.sensor import transcription_sensor

defs = dg.Definitions(
    jobs=[collect_job, comments_job, content_job, enrich_job, reprocess_job, transcribe_job],
    schedules=[collection_schedule],
    sensors=[comments_sensor, content_sensor, enrichment_sensor, transcription_sensor],
    resources={
        "database": DatabaseResource(),
        "app_config": AppConfigResource(),
    },
)
