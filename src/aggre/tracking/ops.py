from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.tracking.model import StageTracking
from aggre.tracking.status import COOLDOWN_SECONDS, MAX_RETRIES, Stage, StageStatus
from aggre.utils.db import now_iso


def upsert_done(engine: sa.engine.Engine, source: str, external_id: str, stage: Stage) -> None:
    """Mark stage as done. Creates row if first attempt, updates if retrying."""
    ts = now_iso()
    stmt = pg_insert(StageTracking).values(
        source=source,
        external_id=external_id,
        stage=stage,
        status=StageStatus.DONE,
        last_ran_at=ts,
        completed_at=ts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "external_id", "stage"],
        set_={
            "status": StageStatus.DONE,
            "last_ran_at": ts,
            "completed_at": ts,
            "error": None,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def upsert_failed(engine: sa.engine.Engine, source: str, external_id: str, stage: Stage, error: str) -> None:
    """Mark stage as failed. Increments retries counter."""
    ts = now_iso()
    stmt = pg_insert(StageTracking).values(
        source=source,
        external_id=external_id,
        stage=stage,
        status=StageStatus.FAILED,
        error=error,
        retries=1,
        last_ran_at=ts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "external_id", "stage"],
        set_={
            "status": StageStatus.FAILED,
            "error": error,
            "retries": StageTracking.retries + 1,
            "last_ran_at": ts,
            "completed_at": None,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def upsert_skipped(engine: sa.engine.Engine, source: str, external_id: str, stage: Stage, reason: str) -> None:
    """Mark stage as skipped with a reason."""
    ts = now_iso()
    stmt = pg_insert(StageTracking).values(
        source=source,
        external_id=external_id,
        stage=stage,
        status=StageStatus.SKIPPED,
        error=reason,
        last_ran_at=ts,
        completed_at=ts,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "external_id", "stage"],
        set_={
            "status": StageStatus.SKIPPED,
            "error": reason,
            "last_ran_at": ts,
            "completed_at": ts,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def retry_filter(tracking_cls: type, stage: Stage) -> sa.ColumnElement[bool]:
    """SQL filter: item is failed, under max retries, and cooldown has passed.

    Works with both StageTracking class and sa.orm.aliased(StageTracking).
    """
    seconds = COOLDOWN_SECONDS[stage]
    return sa.and_(
        tracking_cls.status == StageStatus.FAILED,
        tracking_cls.retries < MAX_RETRIES[stage],
        sa.cast(tracking_cls.last_ran_at, sa.DateTime(timezone=True))
        < (sa.func.now() - sa.literal_column(f"INTERVAL '{seconds} seconds'")),
    )
