"""Drop 'pending' from idx_stage_actionable — PENDING status was never used.

Revision ID: 003
Revises: 002
Create Date: 2026-03-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_stage_actionable", table_name="stage_tracking")
    op.create_index(
        "idx_stage_actionable",
        "stage_tracking",
        ["stage"],
        postgresql_where=sa.text("status = 'failed'"),
    )


def downgrade() -> None:
    op.drop_index("idx_stage_actionable", table_name="stage_tracking")
    op.create_index(
        "idx_stage_actionable",
        "stage_tracking",
        ["stage"],
        postgresql_where=sa.text("status IN ('pending', 'failed')"),
    )
