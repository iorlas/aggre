"""Lobsters collection: op, job, and schedule.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.dagster_defs.collection._shared import _RETRY, collect_source


@dg.op(required_resource_keys={"database", "app_config"}, retry_policy=_RETRY)
def collect_lobsters_op(context: OpExecutionContext) -> int:
    cfg = context.resources.app_config.get_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "lobsters", LobstersCollector)


@dg.job
def collect_lobsters_job() -> None:
    collect_lobsters_op()


collect_lobsters_schedule = dg.ScheduleDefinition(
    name="collect_lobsters_schedule",
    cron_schedule="0 * * * *",
    target=collect_lobsters_job,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
