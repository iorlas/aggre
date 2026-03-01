"""Shared test helpers — replaces _collect() duplicated in 9 files."""

from __future__ import annotations

import sqlalchemy as sa


def collect(collector, engine: sa.engine.Engine, config, settings, log, **kwargs) -> int:
    """Collect references and process them into silver. Returns count of new refs."""
    refs = collector.collect_references(engine, config, settings, log, **kwargs)
    for ref in refs:
        with engine.begin() as conn:
            collector.process_reference(ref["raw_data"], conn, ref["source_id"], log)
    return len(refs)
