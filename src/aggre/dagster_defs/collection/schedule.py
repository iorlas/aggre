"""Collection schedule — hourly source collection."""

import dagster as dg

from aggre.dagster_defs.collection.job import collect_job

collection_schedule = dg.ScheduleDefinition(
    name="hourly_collection",
    cron_schedule="0 * * * *",
    target=collect_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
