"""Collection job -- fetch references from configured sources, then process into silver.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import logging

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors import COLLECTORS
from aggre.config import load_config

logger = logging.getLogger(__name__)


@dg.op(required_resource_keys={"database"}, retry_policy=dg.RetryPolicy(max_retries=2, delay=10))
def collect_all_sources(context: OpExecutionContext) -> int:
    """Collect references from all configured sources, then process into silver."""
    cfg = load_config()
    engine = context.resources.database.get_engine()

    total = 0
    collectors = {name: cls() for name, cls in COLLECTORS.items()}

    for name, collector in collectors.items():
        try:
            source_config = getattr(cfg, name)
            refs = collector.collect_references(engine, source_config, cfg.settings)

            new_count = 0
            for ref in refs:
                try:
                    with engine.begin() as conn:
                        collector.process_reference(ref["raw_data"], conn, ref["source_id"])
                    new_count += 1
                except Exception:
                    logger.exception("collect.process_error source=%s external_id=%s", name, ref["external_id"])

            total += new_count
            logger.info("collect.source_complete source=%s new_observations=%d", name, new_count)
        except Exception:
            logger.exception("collect.source_error source=%s", name)

    context.log.info(f"Collected {total} new observations")
    return total


@dg.job
def collect_job() -> None:
    collect_all_sources()
