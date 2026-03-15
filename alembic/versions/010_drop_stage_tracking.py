"""Drop stage_tracking, add Silver timestamps.

Revision ID: 010
Revises: 009
Create Date: 2026-03-15
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add new columns
    op.add_column("silver_content", sa.Column("discussions_searched_at", sa.Text(), nullable=True))
    op.add_column("silver_discussions", sa.Column("comments_fetched_at", sa.Text(), nullable=True))

    # 2. Replace discussion search index
    op.drop_index("idx_content_needs_discussion_search", table_name="silver_content")
    op.create_index(
        "idx_content_needs_discussion_search",
        "silver_content",
        ["id"],
        postgresql_where=sa.text("discussions_searched_at IS NULL AND text IS NOT NULL"),
    )

    # 3. Backfill comments_fetched_at for already-fetched comments
    op.execute("UPDATE silver_discussions SET comments_fetched_at = now()::text WHERE comments_json IS NOT NULL")

    # 4. Drop stage_tracking table (cascades idx_stage_actionable)
    op.drop_table("stage_tracking")


def downgrade() -> None:
    # Recreate stage_tracking table
    op.create_table(
        "stage_tracking",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retries", sa.Integer(), server_default="0"),
        sa.Column("last_ran_at", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("source", "external_id", "stage"),
    )
    op.create_index(
        "idx_stage_actionable",
        "stage_tracking",
        ["stage"],
        postgresql_where=sa.text("status = 'failed'"),
    )

    # Restore old discussion search index
    op.drop_index("idx_content_needs_discussion_search", table_name="silver_content")
    op.create_index(
        "idx_content_needs_discussion_search",
        "silver_content",
        ["id"],
        postgresql_where=sa.text("text IS NOT NULL AND canonical_url IS NOT NULL"),
    )

    # Drop new columns
    op.drop_column("silver_discussions", "comments_fetched_at")
    op.drop_column("silver_content", "discussions_searched_at")
