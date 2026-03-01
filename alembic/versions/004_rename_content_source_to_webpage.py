"""Rename stage_tracking source='content' to source='webpage'.

The "content" pipeline was renamed to "webpage" to avoid collision with
SilverContent (which holds all content types, not just web pages).

Revision ID: 004
Revises: 003
Create Date: 2026-03-01
"""

from __future__ import annotations

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE stage_tracking SET source = 'webpage' WHERE source = 'content'")


def downgrade() -> None:
    op.execute("UPDATE stage_tracking SET source = 'content' WHERE source = 'webpage'")
