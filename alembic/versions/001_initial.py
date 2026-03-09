"""Initial schema — sources, silver_content, silver_observations.

Revision ID: 001
Revises:
Create Date: 2026-02-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- sources --
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("config", sa.Text, nullable=False),
        sa.Column("enabled", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.Text, server_default=sa.func.now()),
        sa.Column("last_fetched_at", sa.Text, nullable=True),
    )

    # -- silver_content --
    op.create_table(
        "silver_content",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("canonical_url", sa.Text, nullable=False, unique=True),
        sa.Column("domain", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("text", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, server_default=sa.func.now()),
        sa.Column("detected_language", sa.Text, nullable=True),
        sa.Column("enriched_at", sa.Text, nullable=True),
    )
    op.create_index(
        "idx_silver_content_domain",
        "silver_content",
        ["domain"],
        postgresql_where=sa.text("domain IS NOT NULL"),
    )
    op.create_index(
        "idx_content_needs_processing",
        "silver_content",
        ["id"],
        postgresql_where=sa.text("text IS NULL AND error IS NULL"),
    )
    op.create_index(
        "idx_silver_content_enriched_at",
        "silver_content",
        ["enriched_at"],
        postgresql_where=sa.text("enriched_at IS NULL"),
    )

    # -- silver_observations --
    op.create_table(
        "silver_observations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("content_id", sa.Integer, sa.ForeignKey("silver_content.id"), nullable=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("author", sa.Text, nullable=True),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("content_text", sa.Text, nullable=True),
        sa.Column("published_at", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.Text, server_default=sa.func.now()),
        sa.Column("meta", sa.Text, nullable=True),
        sa.Column("comments_json", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("score", sa.Integer, nullable=True),
        sa.Column("comment_count", sa.Integer, nullable=True),
        sa.UniqueConstraint("source_type", "external_id"),
    )
    op.create_index("idx_silver_observations_source_type", "silver_observations", ["source_type"])
    op.create_index("idx_silver_observations_published", "silver_observations", ["published_at"])
    op.create_index("idx_silver_observations_source_id", "silver_observations", ["source_id"])
    op.create_index(
        "idx_silver_observations_external",
        "silver_observations",
        ["source_type", "external_id"],
    )
    op.create_index(
        "idx_observations_needs_comments",
        "silver_observations",
        ["id"],
        postgresql_where=sa.text("comments_json IS NULL AND error IS NULL"),
    )
    op.create_index(
        "idx_silver_observations_url",
        "silver_observations",
        ["url"],
        postgresql_where=sa.text("url IS NOT NULL"),
    )
    op.create_index(
        "idx_silver_observations_content_id",
        "silver_observations",
        ["content_id"],
        postgresql_where=sa.text("content_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("silver_observations")
    op.drop_table("silver_content")
    op.drop_table("sources")
