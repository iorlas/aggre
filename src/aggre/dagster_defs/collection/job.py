"""Collection job -- fetch references from configured sources, then process into silver.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import dagster as dg
from dagster import OpExecutionContext

from aggre.collectors import COLLECTORS
from aggre.config import load_config
from aggre.utils.logging import setup_logging


@dg.op(required_resource_keys={"database"})
def collect_all_sources(context: OpExecutionContext) -> int:
    """Collect references from all configured sources, then process into silver."""
    cfg = load_config()
    engine = context.resources.database.get_engine()
    log = setup_logging(cfg.settings.log_dir, "collect")

    total = 0
    collectors = {name: cls() for name, cls in COLLECTORS.items()}

    for name, collector in collectors.items():
        try:
            source_config = getattr(cfg, name)
            refs = collector.collect_references(engine, source_config, cfg.settings, log)

            new_count = 0
            for ref in refs:
                try:
                    with engine.begin() as conn:
                        collector.process_reference(ref["raw_data"], conn, ref["source_id"], log)
                    new_count += 1
                except Exception:
                    log.exception("collect.process_error", source=name, external_id=ref["external_id"])

            total += new_count
            log.info("collect.source_complete", source=name, new_observations=new_count)
        except Exception:
            log.exception("collect.source_error", source=name)

    context.log.info(f"Collected {total} new observations")
    return total


@dg.job
def collect_job() -> None:
    collect_all_sources()
