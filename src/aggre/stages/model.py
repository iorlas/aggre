from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aggre.db import Base


class StageTracking(Base):
    __tablename__ = "stage_tracking"
    __table_args__ = (sa.UniqueConstraint("source", "external_id", "stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    stage: Mapped[str] = mapped_column(sa.Text, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    retries: Mapped[int] = mapped_column(sa.Integer, server_default="0")
    last_ran_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


# Sensor index: find actionable items for a given stage
sa.Index(
    "idx_stage_actionable",
    StageTracking.stage,
    postgresql_where=StageTracking.status.in_(["pending", "failed"]),
)
