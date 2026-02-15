"""Rename bronze_posts to bronze_discussions and update foreign key references.

Revision ID: 002
Revises: 001
Create Date: 2026-02-15
"""

from __future__ import annotations

from alembic import op

revision: str = "002"
down_revision: str = "001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Rename table
    op.rename_table("bronze_posts", "bronze_discussions")

    # Rename indexes
    op.execute("ALTER INDEX idx_bronze_posts_source_type RENAME TO idx_bronze_discussions_source_type")
    op.execute("ALTER INDEX idx_bronze_posts_external RENAME TO idx_bronze_discussions_external")

    # Rename FK column in silver_discussions
    op.alter_column("silver_discussions", "bronze_post_id", new_column_name="bronze_discussion_id")


def downgrade() -> None:
    op.alter_column("silver_discussions", "bronze_discussion_id", new_column_name="bronze_post_id")

    op.execute("ALTER INDEX idx_bronze_discussions_source_type RENAME TO idx_bronze_posts_source_type")
    op.execute("ALTER INDEX idx_bronze_discussions_external RENAME TO idx_bronze_posts_external")

    op.rename_table("bronze_discussions", "bronze_posts")
