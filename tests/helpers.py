"""Shared test helpers — replaces _collect() duplicated in 9 files."""

from __future__ import annotations

import sqlalchemy as sa

from aggre.db import SilverContent, SilverDiscussion, Source


def collect(collector, engine: sa.engine.Engine, config, settings, **kwargs) -> int:
    """Collect discussions and process them into silver. Returns count of new refs."""
    refs = collector.collect_discussions(engine, config, settings, **kwargs)
    for ref in refs:
        with engine.begin() as conn:
            collector.process_discussion(ref["raw_data"], conn, ref["source_id"])
    return len(refs)


def get_discussions(engine: sa.engine.Engine, **filters) -> list[sa.Row]:
    """Query SilverDiscussion rows, optionally filtering by column values."""
    stmt = sa.select(SilverDiscussion)
    for col, val in filters.items():
        stmt = stmt.where(getattr(SilverDiscussion, col) == val)
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
