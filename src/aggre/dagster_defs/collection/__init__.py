"""Collection jobs and schedules registry — re-exports for dagster_defs."""

from aggre.dagster_defs.collection.arxiv import collect_arxiv_job, collect_arxiv_schedule
from aggre.dagster_defs.collection.hackernews import collect_hackernews_job, collect_hackernews_schedule
from aggre.dagster_defs.collection.huggingface import collect_huggingface_job, collect_huggingface_schedule
from aggre.dagster_defs.collection.lesswrong import collect_lesswrong_job, collect_lesswrong_schedule
from aggre.dagster_defs.collection.lobsters import collect_lobsters_job, collect_lobsters_schedule
from aggre.dagster_defs.collection.reddit import collect_reddit_job, collect_reddit_schedule
from aggre.dagster_defs.collection.rss import collect_rss_job, collect_rss_schedule
from aggre.dagster_defs.collection.telegram import collect_telegram_job, collect_telegram_schedule
from aggre.dagster_defs.collection.youtube import collect_youtube_job, collect_youtube_schedule

__all__ = [
    "collect_arxiv_job",
    "collect_arxiv_schedule",
    "collect_hackernews_job",
    "collect_hackernews_schedule",
    "collect_huggingface_job",
    "collect_huggingface_schedule",
    "collect_lesswrong_job",
    "collect_lesswrong_schedule",
    "collect_lobsters_job",
    "collect_lobsters_schedule",
    "collect_reddit_job",
    "collect_reddit_schedule",
    "collect_rss_job",
    "collect_rss_schedule",
    "collect_telegram_job",
    "collect_telegram_schedule",
    "collect_youtube_job",
    "collect_youtube_schedule",
]
