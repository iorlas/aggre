"""Add transcribed_by column to silver_content.

Revision ID: 009
Revises: 008
Create Date: 2026-03-10
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("silver_content", sa.Column("transcribed_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("silver_content", "transcribed_by")
