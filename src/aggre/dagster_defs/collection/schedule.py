"""Per-source collection schedules — every 3 hours, stopped by default."""

import dagster as dg

from aggre.dagster_defs.collection.job import (
    collect_hackernews_job,
    collect_huggingface_job,
    collect_lobsters_job,
    collect_reddit_job,
    collect_rss_job,
    collect_telegram_job,
    collect_youtube_job,
)

collect_youtube_schedule = dg.ScheduleDefinition(
    name="collect_youtube_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_youtube_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

collect_reddit_schedule = dg.ScheduleDefinition(
    name="collect_reddit_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_reddit_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

collect_hackernews_schedule = dg.ScheduleDefinition(
    name="collect_hackernews_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_hackernews_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

collect_lobsters_schedule = dg.ScheduleDefinition(
    name="collect_lobsters_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_lobsters_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

collect_rss_schedule = dg.ScheduleDefinition(
    name="collect_rss_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_rss_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

collect_huggingface_schedule = dg.ScheduleDefinition(
    name="collect_huggingface_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_huggingface_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)

collect_telegram_schedule = dg.ScheduleDefinition(
    name="collect_telegram_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_telegram_job,
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
