"""Drop discussions_searched_at column and index.

Discussion-search workflow removed — cross-source discovery will be handled
by historical backfill from HN/Lobsters archives instead.

Revision ID: 011
Revises: 010
"""

import sqlalchemy as sa

from alembic import op

revision = "011"
down_revision = "010"


def upgrade() -> None:
    op.drop_index("idx_content_needs_discussion_search", table_name="silver_content")
    op.drop_column("silver_content", "discussions_searched_at")


def downgrade() -> None:
    op.add_column(
        "silver_content",
        sa.Column("discussions_searched_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_content_needs_discussion_search",
        "silver_content",
        ["id"],
        postgresql_where="discussions_searched_at IS NULL AND text IS NOT NULL",
    )
