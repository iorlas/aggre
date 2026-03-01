"""Shared test helpers — replaces _collect() duplicated in 9 files."""

from __future__ import annotations

import sqlalchemy as sa

from aggre.db import SilverContent, SilverObservation, Source
from aggre.tracking.model import StageTracking
from aggre.tracking.status import Stage, StageStatus


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


def assert_tracking(
    engine: sa.engine.Engine,
    source: str,
    external_id: str,
    stage: Stage,
    expected_status: StageStatus,
    *,
    error_contains: str | None = None,
) -> None:
    """Assert a StageTracking row exists with the expected status and optional error substring."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(StageTracking).where(
                StageTracking.source == source,
                StageTracking.external_id == external_id,
                StageTracking.stage == stage,
            )
        ).fetchone()
        assert row is not None, f"No tracking row for {source}/{external_id}/{stage}"
        assert row.status == expected_status, f"Expected status {expected_status}, got {row.status}"
        if error_contains is not None:
            assert row.error is not None and error_contains in row.error, f"Expected error containing {error_contains!r}, got {row.error!r}"


def assert_no_tracking(
    engine: sa.engine.Engine,
    source: str,
    external_id: str,
    stage: Stage,
) -> None:
    """Assert no StageTracking row exists for the given key."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(StageTracking).where(
                StageTracking.source == source,
                StageTracking.external_id == external_id,
                StageTracking.stage == stage,
            )
        ).fetchone()
        assert row is None, f"Unexpected tracking row for {source}/{external_id}/{stage}"
