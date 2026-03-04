"""Reprocess job -- rebuild silver from bronze without hitting external APIs.

Note: ``from __future__ import annotations`` is omitted because Dagster's
``@op`` decorator inspects context-parameter type hints at decoration time and
cannot resolve deferred (stringified) annotations.
"""

import json
import logging
from pathlib import Path

import dagster as dg
import sqlalchemy as sa
from dagster import OpExecutionContext

from aggre.collectors import COLLECTORS
from aggre.utils.bronze import DEFAULT_BRONZE_ROOT

logger = logging.getLogger(__name__)


def reprocess_from_bronze(
    engine: sa.engine.Engine,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> int:
    """Scan bronze ref.json files and rebuild silver via process_discussion.

    For each source type, iterates bronze/{source_type}/*/raw.json,
    loads the raw data, and calls the collector's process_discussion().
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
                    collector.process_discussion(raw_data, conn, source_id)
                reprocessed += 1
            except Exception:
                ext_id = ref_file.parent.name
                logger.exception("reprocess.ref_error source=%s external_id=%s", source_type, ext_id)

        total += reprocessed
        logger.info("reprocess.source_complete source=%s reprocessed=%d", source_type, reprocessed)

    return total


@dg.op(required_resource_keys={"database"})
def reprocess_bronze_op(context: OpExecutionContext) -> int:
    """Rebuild silver from bronze ref.json files."""
    engine = context.resources.database.get_engine()
    count = reprocess_from_bronze(engine)
    logger.info("reprocess.complete discussions=%d", count)
    return count


@dg.job
def reprocess_job() -> None:
    reprocess_bronze_op()
