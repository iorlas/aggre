"""Rename enrich stage to discussion_search.

The "enrichment" stage searches other platforms (HN, Lobsters) for
discussions about collected URLs.  "discussion_search" is more descriptive.

Revision ID: 007
Revises: 006
Create Date: 2026-03-01
"""

from __future__ import annotations

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename the partial index on silver_content
    op.execute(
        "ALTER INDEX idx_content_needs_enrich "
        "RENAME TO idx_content_needs_discussion_search"
    )

    # Update existing stage_tracking rows
    op.execute(
        "UPDATE stage_tracking SET stage = 'discussion_search' "
        "WHERE stage = 'enrich'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE stage_tracking SET stage = 'enrich' "
        "WHERE stage = 'discussion_search'"
    )

    op.execute(
        "ALTER INDEX idx_content_needs_discussion_search "
        "RENAME TO idx_content_needs_enrich"
    )
