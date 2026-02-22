"""Simplify transcription_status: remove intermediate states.

Convert any 'downloading' or 'transcribing' rows back to 'pending'
since the transcriber now transitions directly from pending to completed/failed.

Revision ID: 002
Revises: 001
Create Date: 2026-02-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE silver_content SET transcription_status = 'pending' "
            "WHERE transcription_status IN ('downloading', 'transcribing')"
        )
    )


def downgrade() -> None:
    pass  # No-op: intermediate states are informational only
