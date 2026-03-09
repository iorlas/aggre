"""Rename silver_observations → silver_discussions.

Unifies terminology: the codebase used "observation" in the DB layer
and "reference" in the collector layer for the same domain concept.
Everything is now "discussion".

Revision ID: 006
Revises: 005
Create Date: 2026-03-01
"""

from __future__ import annotations

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("silver_observations", "silver_discussions")

    # Rename indexes
    op.execute("ALTER INDEX idx_silver_observations_source_type RENAME TO idx_silver_discussions_source_type")
    op.execute("ALTER INDEX idx_silver_observations_published RENAME TO idx_silver_discussions_published")
    op.execute("ALTER INDEX idx_silver_observations_source_id RENAME TO idx_silver_discussions_source_id")
    op.execute("ALTER INDEX idx_silver_observations_external RENAME TO idx_silver_discussions_external")
    op.execute("ALTER INDEX idx_observations_comments_null RENAME TO idx_discussions_comments_null")
    op.execute("ALTER INDEX idx_silver_observations_url RENAME TO idx_silver_discussions_url")
    op.execute("ALTER INDEX idx_silver_observations_content_id RENAME TO idx_silver_discussions_content_id")

    # Rename unique constraint
    op.execute(
        "ALTER TABLE silver_discussions RENAME CONSTRAINT "
        "silver_observations_source_type_external_id_key TO "
        "silver_discussions_source_type_external_id_key"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE silver_discussions RENAME CONSTRAINT "
        "silver_discussions_source_type_external_id_key TO "
        "silver_observations_source_type_external_id_key"
    )

    op.execute("ALTER INDEX idx_silver_discussions_source_type RENAME TO idx_silver_observations_source_type")
    op.execute("ALTER INDEX idx_silver_discussions_published RENAME TO idx_silver_observations_published")
    op.execute("ALTER INDEX idx_silver_discussions_source_id RENAME TO idx_silver_observations_source_id")
    op.execute("ALTER INDEX idx_silver_discussions_external RENAME TO idx_silver_observations_external")
    op.execute("ALTER INDEX idx_discussions_comments_null RENAME TO idx_observations_comments_null")
    op.execute("ALTER INDEX idx_silver_discussions_url RENAME TO idx_silver_observations_url")
    op.execute("ALTER INDEX idx_silver_discussions_content_id RENAME TO idx_silver_observations_content_id")

    op.rename_table("silver_discussions", "silver_observations")
