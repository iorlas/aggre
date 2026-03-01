"""Reddit collection: op, job, and schedule.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors.reddit.collector import RedditCollector
from aggre.dagster_defs.collection._shared import _RETRY, collect_source


@dg.op(required_resource_keys={"database", "app_config"}, retry_policy=_RETRY)
def collect_reddit_op(context: OpExecutionContext) -> int:
    cfg = context.resources.app_config.get_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "reddit", RedditCollector)


@dg.job
def collect_reddit_job() -> None:
    collect_reddit_op()


collect_reddit_schedule = dg.ScheduleDefinition(
    name="collect_reddit_schedule",
    cron_schedule="0 * * * *",
    target=collect_reddit_job,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
