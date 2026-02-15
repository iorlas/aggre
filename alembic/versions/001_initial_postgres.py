"""Initial PostgreSQL schema.

Revision ID: 001
Revises: None
Create Date: 2026-02-14
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
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("config", sa.Text, nullable=False),
        sa.Column("enabled", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.Text, server_default=sa.func.now()),
        sa.Column("last_fetched_at", sa.Text, nullable=True),
    )

    op.create_table(
        "bronze_posts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("raw_data", sa.Text, nullable=False),
        sa.Column("fetched_at", sa.Text, server_default=sa.func.now()),
        sa.UniqueConstraint("source_type", "external_id"),
    )

    op.create_table(
        "silver_content",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("canonical_url", sa.Text, nullable=False, unique=True),
        sa.Column("domain", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("body_text", sa.Text, nullable=True),
        sa.Column("fetch_status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("fetch_error", sa.Text, nullable=True),
        sa.Column("fetched_at", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, server_default=sa.func.now()),
        # Transcription fields (content-level concern)
        sa.Column("transcription_status", sa.Text, nullable=True),
        sa.Column("transcription_error", sa.Text, nullable=True),
        sa.Column("detected_language", sa.Text, nullable=True),
        # Enrichment tracking
        sa.Column("enriched_at", sa.Text, nullable=True),
    )

    op.create_table(
        "silver_discussions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("bronze_post_id", sa.Integer, sa.ForeignKey("bronze_posts.id"), nullable=True),
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
        sa.Column("comments_status", sa.Text, nullable=True),
        sa.Column("comments_json", sa.Text, nullable=True),
        sa.Column("score", sa.Integer, nullable=True),
        sa.Column("comment_count", sa.Integer, nullable=True),
        sa.UniqueConstraint("source_type", "external_id"),
    )

    # Bronze indexes
    op.create_index("idx_bronze_posts_source_type", "bronze_posts", ["source_type"])
    op.create_index("idx_bronze_posts_external", "bronze_posts", ["source_type", "external_id"])

    # SilverContent indexes
    op.create_index(
        "idx_silver_content_domain", "silver_content", ["domain"],
        postgresql_where=sa.text("domain IS NOT NULL"),
    )
    op.create_index("idx_silver_content_fetch_status", "silver_content", ["fetch_status"])
    op.create_index(
        "idx_silver_content_transcription", "silver_content", ["transcription_status"],
        postgresql_where=sa.text("transcription_status IS NOT NULL"),
    )
    op.create_index(
        "idx_silver_content_enriched_at", "silver_content", ["enriched_at"],
        postgresql_where=sa.text("enriched_at IS NULL"),
    )

    # SilverDiscussion indexes
    op.create_index("idx_silver_discussions_source_type", "silver_discussions", ["source_type"])
    op.create_index("idx_silver_discussions_published", "silver_discussions", ["published_at"])
    op.create_index("idx_silver_discussions_source_id", "silver_discussions", ["source_id"])
    op.create_index("idx_silver_discussions_external", "silver_discussions", ["source_type", "external_id"])
    op.create_index(
        "idx_silver_discussions_comments_status", "silver_discussions", ["comments_status"],
        postgresql_where=sa.text("comments_status IS NOT NULL"),
    )
    op.create_index(
        "idx_silver_discussions_url", "silver_discussions", ["url"],
        postgresql_where=sa.text("url IS NOT NULL"),
    )
    op.create_index(
        "idx_silver_discussions_content_id", "silver_discussions", ["content_id"],
        postgresql_where=sa.text("content_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("silver_discussions")
    op.drop_table("silver_content")
    op.drop_table("bronze_posts")
    op.drop_table("sources")
