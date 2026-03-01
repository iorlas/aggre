"""Shared test helpers — replaces _collect() duplicated in 9 files."""

from __future__ import annotations

import sqlalchemy as sa

from aggre.db import SilverContent, SilverObservation, Source


def collect(collector, engine: sa.engine.Engine, config, settings, **kwargs) -> int:
    """Collect references and process them into silver. Returns count of new refs."""
    refs = collector.collect_references(engine, config, settings, **kwargs)
    for ref in refs:
        with engine.begin() as conn:
            collector.process_reference(ref["raw_data"], conn, ref["source_id"])
    return len(refs)


def get_observations(engine: sa.engine.Engine, **filters) -> list[sa.Row]:
    """Query SilverObservation rows, optionally filtering by column values."""
    stmt = sa.select(SilverObservation)
    for col, val in filters.items():
        stmt = stmt.where(getattr(SilverObservation, col) == val)
    with engine.connect() as conn:
        return conn.execute(stmt).fetchall()


def get_contents(engine: sa.engine.Engine, **filters) -> list[sa.Row]:
    """Query SilverContent rows, optionally filtering by column values."""
    stmt = sa.select(SilverContent)
    for col, val in filters.items():
        stmt = stmt.where(getattr(SilverContent, col) == val)
    with engine.connect() as conn:
        return conn.execute(stmt).fetchall()


def get_sources(engine: sa.engine.Engine, **filters) -> list[sa.Row]:
    """Query Source rows, optionally filtering by column values."""
    stmt = sa.select(Source)
    for col, val in filters.items():
        stmt = stmt.where(getattr(Source, col) == val)
    with engine.connect() as conn:
        return conn.execute(stmt).fetchall()
