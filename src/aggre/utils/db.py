"""Generic SQLAlchemy helpers — engine factory and timestamp utilities."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa


def now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def get_engine(database_url: str) -> sa.engine.Engine:
    """Create a SQLAlchemy engine for the given database URL."""
    return sa.create_engine(database_url, echo=False)
