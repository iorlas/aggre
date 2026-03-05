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
from aggre.utils.bronze import DEFAULT_BRONZE_ROOT, _store_for

logger = logging.getLogger(__name__)


def reprocess_from_bronze(
    engine: sa.engine.Engine,
    bronze_root: Path = DEFAULT_BRONZE_ROOT,
) -> int:
    """Scan bronze ref.json files and rebuild silver via process_discussion.

    For each source type, lists bronze keys matching {source_type}/*/raw.json,
    loads the raw data, and calls the collector's process_discussion().
    Returns total count of references reprocessed.
    """
    store = _store_for(bronze_root)
    total = 0

    for config_name, collector_cls in COLLECTORS.items():
        collector = collector_cls()
        source_type = collector.source_type

        # List all keys under this source type and filter for raw.json
        all_keys = store.list_keys(f"{source_type}/")
        raw_keys = sorted(k for k in all_keys if k.endswith("/raw.json"))
        if not raw_keys:
            continue

        # Ensure source row exists
        source_id = collector._ensure_source(engine, source_type)

        reprocessed = 0
        for key in raw_keys:
            try:
                raw_data = json.loads(store.read(key))
                with engine.begin() as conn:
                    collector.process_discussion(raw_data, conn, source_id)
                reprocessed += 1
            except Exception:
                # Extract external_id from key: "hackernews/12345/raw.json" -> "12345"
                parts = key.split("/")
                ext_id = parts[1] if len(parts) >= 2 else key
                logger.exception("reprocess.ref_error source=%s external_id=%s", source_type, ext_id)

        total += reprocessed
        logger.info("reprocess.source_complete source=%s reprocessed=%d", source_type, reprocessed)

    return total


@dg.op(required_resource_keys={"database"})
def reprocess_bronze_op(context: OpExecutionContext) -> int:  # pragma: no cover — Dagster op wiring
    """Rebuild silver from bronze ref.json files."""
    engine = context.resources.database.get_engine()
    count = reprocess_from_bronze(engine)
    logger.info("reprocess.complete discussions=%d", count)
    return count


@dg.job
def reprocess_job() -> None:
    reprocess_bronze_op()
