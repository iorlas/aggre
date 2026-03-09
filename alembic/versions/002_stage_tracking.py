"""Stage tracking — standardize processing state across pipeline stages.

Revision ID: 002
Revises: 001
Create Date: 2026-03-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- stage_tracking table --
    op.create_table(
        "stage_tracking",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("retries", sa.Integer, server_default="0"),
        sa.Column("last_ran_at", sa.Text, nullable=True),
        sa.Column("completed_at", sa.Text, nullable=True),
        sa.UniqueConstraint("source", "external_id", "stage"),
    )
    op.create_index(
        "idx_stage_actionable",
        "stage_tracking",
        ["stage"],
        postgresql_where=sa.text("status IN ('pending', 'failed')"),
    )

    # -- silver_content: drop old processing columns --
    op.drop_index("idx_content_needs_processing", table_name="silver_content")
    op.drop_index("idx_silver_content_enriched_at", table_name="silver_content")
    op.drop_column("silver_content", "error")
    op.drop_column("silver_content", "fetched_at")
    op.drop_column("silver_content", "enriched_at")

    # -- silver_content: new indexes for sensor discovery --
    op.create_index(
        "idx_content_text_null",
        "silver_content",
        ["id"],
        postgresql_where=sa.text("text IS NULL"),
    )
    op.create_index(
        "idx_content_needs_enrich",
        "silver_content",
        ["id"],
        postgresql_where=sa.text("text IS NOT NULL AND canonical_url IS NOT NULL"),
    )

    # -- silver_observations: drop old processing column --
    op.drop_index("idx_observations_needs_comments", table_name="silver_observations")
    op.drop_column("silver_observations", "error")

    # -- silver_observations: new index for sensor discovery --
    op.create_index(
        "idx_observations_comments_null",
        "silver_observations",
        ["id"],
        postgresql_where=sa.text("comments_json IS NULL"),
    )


def downgrade() -> None:
    # -- silver_observations: restore error column and old index --
    op.drop_index("idx_observations_comments_null", table_name="silver_observations")
    op.add_column("silver_observations", sa.Column("error", sa.Text, nullable=True))
    op.create_index(
        "idx_observations_needs_comments",
        "silver_observations",
        ["id"],
        postgresql_where=sa.text("comments_json IS NULL AND error IS NULL"),
    )

    # -- silver_content: restore old columns and indexes --
    op.drop_index("idx_content_needs_enrich", table_name="silver_content")
    op.drop_index("idx_content_text_null", table_name="silver_content")
    op.add_column("silver_content", sa.Column("enriched_at", sa.Text, nullable=True))
    op.add_column("silver_content", sa.Column("fetched_at", sa.Text, nullable=True))
    op.add_column("silver_content", sa.Column("error", sa.Text, nullable=True))
    op.create_index(
        "idx_silver_content_enriched_at",
        "silver_content",
        ["enriched_at"],
        postgresql_where=sa.text("enriched_at IS NULL"),
    )
    op.create_index(
        "idx_content_needs_processing",
        "silver_content",
        ["id"],
        postgresql_where=sa.text("text IS NULL AND error IS NULL"),
    )

    # -- drop stage_tracking --
    op.drop_index("idx_stage_actionable", table_name="stage_tracking")
    op.drop_table("stage_tracking")
