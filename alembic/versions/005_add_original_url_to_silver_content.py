"""Add original_url column to silver_content.

Preserves the original URL from the collector (before normalization)
so HTTP fetches use the working URL while canonical_url stays for dedup.

Revision ID: 005
Revises: 004
Create Date: 2026-03-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("silver_content", sa.Column("original_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("silver_content", "original_url")
