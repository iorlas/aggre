"""Telegram collection: op, job, and schedule.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors.telegram.collector import TelegramCollector
from aggre.dagster_defs.collection._shared import _RETRY, collect_source


@dg.op(required_resource_keys={"database", "app_config"}, retry_policy=_RETRY)
def collect_telegram_op(context: OpExecutionContext) -> int:  # pragma: no cover — Dagster op wiring
    cfg = context.resources.app_config.get_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "telegram", TelegramCollector)


@dg.job
def collect_telegram_job() -> None:
    collect_telegram_op()


collect_telegram_schedule = dg.ScheduleDefinition(
    name="collect_telegram_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_telegram_job,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
