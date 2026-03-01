"""Dagster definitions for Aggre pipeline."""

import dagster as dg

from aggre.dagster_defs.collection import (
    collect_hackernews_job,
    collect_hackernews_schedule,
    collect_huggingface_job,
    collect_huggingface_schedule,
    collect_lobsters_job,
    collect_lobsters_schedule,
    collect_reddit_job,
    collect_reddit_schedule,
    collect_rss_job,
    collect_rss_schedule,
    collect_telegram_job,
    collect_telegram_schedule,
    collect_youtube_job,
    collect_youtube_schedule,
)
from aggre.dagster_defs.comments.job import comments_job
from aggre.dagster_defs.comments.sensor import comments_sensor
from aggre.dagster_defs.discussion_search.job import discussion_search_job
from aggre.dagster_defs.discussion_search.sensor import discussion_search_sensor
from aggre.dagster_defs.reprocess.job import reprocess_job
from aggre.dagster_defs.resources import AppConfigResource, DatabaseResource
from aggre.dagster_defs.transcription.job import transcribe_job
from aggre.dagster_defs.transcription.sensor import transcription_sensor
from aggre.dagster_defs.webpage.job import webpage_job
from aggre.dagster_defs.webpage.sensor import webpage_sensor

defs = dg.Definitions(
    jobs=[
        collect_youtube_job,
        collect_reddit_job,
        collect_hackernews_job,
        collect_lobsters_job,
        collect_rss_job,
        collect_huggingface_job,
        collect_telegram_job,
        comments_job,
        webpage_job,
        discussion_search_job,
        reprocess_job,
        transcribe_job,
    ],
    schedules=[
        collect_youtube_schedule,
        collect_reddit_schedule,
        collect_hackernews_schedule,
        collect_lobsters_schedule,
        collect_rss_schedule,
        collect_huggingface_schedule,
        collect_telegram_schedule,
    ],
    sensors=[comments_sensor, webpage_sensor, discussion_search_sensor, transcription_sensor],
    resources={
        "database": DatabaseResource(),
        "app_config": AppConfigResource(),
    },
)
