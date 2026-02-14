"""Initial schema.

Revision ID: 001
Revises: None
Create Date: 2025-01-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("config", sa.Text, nullable=False),
        sa.Column("enabled", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.Text, server_default=sa.text("(datetime('now'))")),
        sa.Column("last_fetched_at", sa.Text, nullable=True),
    )

    op.create_table(
        "raw_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("raw_data", sa.Text, nullable=False),
        sa.Column("fetched_at", sa.Text, server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("source_type", "external_id"),
    )

    op.create_table(
        "content_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("raw_item_id", sa.Integer, sa.ForeignKey("raw_items.id"), nullable=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("author", sa.Text, nullable=True),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("content_text", sa.Text, nullable=True),
        sa.Column("published_at", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.Text, server_default=sa.text("(datetime('now'))")),
        sa.Column("metadata", sa.Text, nullable=True),
        sa.Column("transcription_status", sa.Text, nullable=True),
        sa.Column("transcription_error", sa.Text, nullable=True),
        sa.Column("detected_language", sa.Text, nullable=True),
        sa.UniqueConstraint("source_type", "external_id"),
    )

    op.create_table(
        "raw_comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("raw_item_id", sa.Integer, sa.ForeignKey("raw_items.id"), nullable=True),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("raw_data", sa.Text, nullable=False),
        sa.Column("fetched_at", sa.Text, server_default=sa.text("(datetime('now'))")),
        sa.UniqueConstraint("external_id"),
    )

    op.create_table(
        "reddit_comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("content_item_id", sa.Integer, sa.ForeignKey("content_items.id"), nullable=True),
        sa.Column("raw_comment_id", sa.Integer, sa.ForeignKey("raw_comments.id"), nullable=True),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("author", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("score", sa.Integer, nullable=True),
        sa.Column("parent_id", sa.Text, nullable=True),
        sa.Column("depth", sa.Integer, nullable=True),
        sa.Column("created_at", sa.Text, nullable=True),
        sa.UniqueConstraint("external_id"),
    )

    # Bronze indexes
    op.create_index("idx_raw_source_type", "raw_items", ["source_type"])
    op.create_index("idx_raw_external", "raw_items", ["source_type", "external_id"])

    # Silver indexes
    op.create_index("idx_content_source_type", "content_items", ["source_type"])
    op.create_index("idx_content_published", "content_items", ["published_at"])
    op.create_index("idx_content_source_id", "content_items", ["source_id"])
    op.create_index("idx_content_external", "content_items", ["source_type", "external_id"])
    op.create_index(
        "idx_content_transcription",
        "content_items",
        ["transcription_status"],
        sqlite_where=sa.text("transcription_status IS NOT NULL"),
    )
    op.create_index("idx_comments_content_item", "reddit_comments", ["content_item_id"])


def downgrade() -> None:
    op.drop_table("reddit_comments")
    op.drop_table("raw_comments")
    op.drop_table("content_items")
    op.drop_table("raw_items")
    op.drop_table("sources")
