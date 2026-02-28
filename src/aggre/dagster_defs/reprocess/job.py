"""Reprocess job -- rebuild silver from bronze without hitting external APIs.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import json
from pathlib import Path

import dagster as dg
import sqlalchemy as sa
import structlog
from dagster import OpExecutionContext

from aggre.collectors import COLLECTORS
from aggre.utils.bronze import DEFAULT_BRONZE_ROOT
from aggre.utils.logging import setup_logging


def reprocess_from_bronze(
    engine: sa.engine.Engine,
    log: structlog.stdlib.BoundLogger,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> int:
    """Scan bronze ref.json files and rebuild silver via process_reference.

    For each source type, iterates bronze/{source_type}/*/raw.json,
    loads the raw data, and calls the collector's process_reference().
    Returns total count of references reprocessed.
    """
    total = 0

    for config_name, collector_cls in COLLECTORS.items():
        collector = collector_cls()
        source_type = collector.source_type
        source_dir = bronze_root / source_type

        if not source_dir.exists():
            continue

        # Find all raw.json files under this source type
        ref_files = sorted(source_dir.glob("*/raw.json"))
        if not ref_files:
            continue

        # Ensure source row exists
        source_id = collector._ensure_source(engine, source_type)

        reprocessed = 0
        for ref_file in ref_files:
            try:
                raw_data = json.loads(ref_file.read_text())
                with engine.begin() as conn:
                    collector.process_reference(raw_data, conn, source_id, log)
                reprocessed += 1
            except Exception:
                ext_id = ref_file.parent.name
                log.exception("reprocess.ref_error", source=source_type, external_id=ext_id)

        total += reprocessed
        log.info("reprocess.source_complete", source=source_type, reprocessed=reprocessed)

    return total


@dg.op(required_resource_keys={"database"})
def reprocess_bronze_op(context: OpExecutionContext) -> int:
    """Rebuild silver from bronze ref.json files."""
    from aggre.config import load_config

    cfg = load_config()
    engine = context.resources.database.get_engine()
    log = setup_logging(cfg.settings.log_dir, "reprocess")
    count = reprocess_from_bronze(engine, log)
    context.log.info(f"Reprocessed {count} references from bronze")
    return count


@dg.job
def reprocess_job() -> None:
    reprocess_bronze_op()
