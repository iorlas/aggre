"""Add raw_html column to silver_content table.

Revision ID: 003
Revises: 002
Create Date: 2026-02-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str = "002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("silver_content", sa.Column("raw_html", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("silver_content", "raw_html")
