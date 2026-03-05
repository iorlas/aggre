"""HuggingFace collection: op, job, and schedule.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors.huggingface.collector import HuggingfaceCollector
from aggre.dagster_defs.collection._shared import _RETRY, collect_source


@dg.op(required_resource_keys={"database", "app_config"}, retry_policy=_RETRY)
def collect_huggingface_op(context: OpExecutionContext) -> int:  # pragma: no cover — Dagster op wiring
    cfg = context.resources.app_config.get_config()
    engine = context.resources.database.get_engine()
    return collect_source(engine, cfg, "huggingface", HuggingfaceCollector)


@dg.job
def collect_huggingface_job() -> None:
    collect_huggingface_op()


collect_huggingface_schedule = dg.ScheduleDefinition(
    name="collect_huggingface_schedule",
    cron_schedule="0 */3 * * *",
    target=collect_huggingface_job,
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
