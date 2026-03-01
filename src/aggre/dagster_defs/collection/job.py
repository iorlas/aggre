"""Per-source collection jobs -- one Dagster job per source for natural parallelism.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import logging

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.huggingface.collector import HuggingfaceCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.reddit.collector import RedditCollector
from aggre.collectors.rss.collector import RssCollector
from aggre.collectors.telegram.collector import TelegramCollector
from aggre.collectors.youtube.collector import YoutubeCollector
from aggre.config import load_config

logger = logging.getLogger(__name__)

_RETRY = dg.RetryPolicy(max_retries=2, delay=10)


def collect_source(engine, cfg, name: str, collector_cls) -> int:
    """Collect discussions for one source, process into silver. Returns count."""
    source_config = getattr(cfg, name)
    collector = collector_cls()
    refs = collector.collect_discussions(engine, source_config, cfg.settings)
    count = 0
    for ref in refs:
        try:
            with engine.begin() as conn:
                collector.process_discussion(ref["raw_data"], conn, ref["source_id"])
            count += 1
        except Exception:
            logger.exception("collect.process_error source=%s external_id=%s", name, ref["external_id"])
    logger.info("collect.source_complete source=%s new_discussions=%d", name, count)
    return count


# -- youtube ------------------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=_RETRY)
def collect_youtube_op(context: OpExecutionContext) -> int:
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "youtube", YoutubeCollector)


@dg.job
def collect_youtube_job() -> None:
    collect_youtube_op()


# -- reddit -------------------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=_RETRY)
def collect_reddit_op(context: OpExecutionContext) -> int:
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "reddit", RedditCollector)


@dg.job
def collect_reddit_job() -> None:
    collect_reddit_op()


# -- hackernews ---------------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=_RETRY)
def collect_hackernews_op(context: OpExecutionContext) -> int:
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "hackernews", HackernewsCollector)


@dg.job
def collect_hackernews_job() -> None:
    collect_hackernews_op()


# -- lobsters -----------------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=_RETRY)
def collect_lobsters_op(context: OpExecutionContext) -> int:
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "lobsters", LobstersCollector)


@dg.job
def collect_lobsters_job() -> None:
    collect_lobsters_op()


# -- rss ----------------------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=_RETRY)
def collect_rss_op(context: OpExecutionContext) -> int:
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "rss", RssCollector)


@dg.job
def collect_rss_job() -> None:
    collect_rss_op()


# -- huggingface --------------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=_RETRY)
def collect_huggingface_op(context: OpExecutionContext) -> int:
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "huggingface", HuggingfaceCollector)


@dg.job
def collect_huggingface_job() -> None:
    collect_huggingface_op()


# -- telegram -----------------------------------------------------------------


@dg.op(required_resource_keys={"database"}, retry_policy=_RETRY)
def collect_telegram_op(context: OpExecutionContext) -> int:
    cfg = load_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "telegram", TelegramCollector)


@dg.job
def collect_telegram_job() -> None:
    collect_telegram_op()
