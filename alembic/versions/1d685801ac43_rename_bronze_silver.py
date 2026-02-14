"""rename_bronze_silver

Revision ID: 1d685801ac43
Revises: 001
Create Date: 2026-02-14 05:08:18.505439
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d685801ac43"
down_revision: str | None = "001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Disable FK checks during migration (SQLite requires this for table recreation)
    op.execute("PRAGMA foreign_keys=OFF")

    # ---- Drop old indexes ----
    op.drop_index("idx_raw_source_type", table_name="raw_items")
    op.drop_index("idx_raw_external", table_name="raw_items")
    op.drop_index("idx_content_source_type", table_name="content_items")
    op.drop_index("idx_content_published", table_name="content_items")
    op.drop_index("idx_content_source_id", table_name="content_items")
    op.drop_index("idx_content_external", table_name="content_items")
    op.drop_index("idx_content_transcription", table_name="content_items")
    op.drop_index("idx_comments_content_item", table_name="reddit_comments")

    # ---- bronze_posts (was raw_items) ----
    # Simple rename — no column or FK changes needed
    op.rename_table("raw_items", "bronze_posts")

    # ---- bronze_comments (was raw_comments) ----
    # Column rename: raw_item_id → bronze_post_id, FK target updated
    op.create_table(
        "bronze_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bronze_post_id", sa.Integer(), sa.ForeignKey("bronze_posts.id"), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("raw_data", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), server_default=sa.text("(datetime('now'))"), nullable=True),
        sa.UniqueConstraint("external_id"),
    )
    op.execute(
        "INSERT INTO bronze_comments (id, bronze_post_id, external_id, raw_data, fetched_at) "
        "SELECT id, raw_item_id, external_id, raw_data, fetched_at FROM raw_comments"
    )
    op.drop_table("raw_comments")

    # ---- silver_posts (was content_items) ----
    # Column renames: raw_item_id → bronze_post_id, metadata → meta; FK targets updated
    op.create_table(
        "silver_posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("bronze_post_id", sa.Integer(), sa.ForeignKey("bronze_posts.id"), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.Text(), server_default=sa.text("(datetime('now'))"), nullable=True),
        sa.Column("meta", sa.Text(), nullable=True),
        sa.Column("transcription_status", sa.Text(), nullable=True),
        sa.Column("transcription_error", sa.Text(), nullable=True),
        sa.Column("detected_language", sa.Text(), nullable=True),
        sa.UniqueConstraint("source_type", "external_id"),
    )
    op.execute(
        "INSERT INTO silver_posts "
        "(id, source_id, bronze_post_id, source_type, external_id, title, author, url, "
        "content_text, published_at, fetched_at, meta, transcription_status, transcription_error, detected_language) "
        "SELECT id, source_id, raw_item_id, source_type, external_id, title, author, url, "
        "content_text, published_at, fetched_at, metadata, transcription_status, transcription_error, detected_language "
        "FROM content_items"
    )
    op.drop_table("content_items")

    # ---- silver_comments (was reddit_comments) ----
    # Column renames: content_item_id → silver_post_id, raw_comment_id → bronze_comment_id; FK targets updated
    op.create_table(
        "silver_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("silver_post_id", sa.Integer(), sa.ForeignKey("silver_posts.id"), nullable=True),
        sa.Column("bronze_comment_id", sa.Integer(), sa.ForeignKey("bronze_comments.id"), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("parent_id", sa.Text(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("external_id"),
    )
    op.execute(
        "INSERT INTO silver_comments "
        "(id, silver_post_id, bronze_comment_id, external_id, author, body, score, parent_id, depth, created_at) "
        "SELECT id, content_item_id, raw_comment_id, external_id, author, body, score, parent_id, depth, created_at "
        "FROM reddit_comments"
    )
    op.drop_table("reddit_comments")

    # ---- Create new indexes ----
    # Bronze
    op.create_index("idx_bronze_posts_source_type", "bronze_posts", ["source_type"])
    op.create_index("idx_bronze_posts_external", "bronze_posts", ["source_type", "external_id"])
    # Silver
    op.create_index("idx_silver_posts_source_type", "silver_posts", ["source_type"])
    op.create_index("idx_silver_posts_published", "silver_posts", ["published_at"])
    op.create_index("idx_silver_posts_source_id", "silver_posts", ["source_id"])
    op.create_index("idx_silver_posts_external", "silver_posts", ["source_type", "external_id"])
    op.create_index(
        "idx_silver_posts_transcription",
        "silver_posts",
        ["transcription_status"],
        sqlite_where=sa.text("transcription_status IS NOT NULL"),
    )
    op.create_index("idx_silver_comments_post_id", "silver_comments", ["silver_post_id"])

    # Re-enable FK checks
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")

    # ---- Drop new indexes ----
    op.drop_index("idx_bronze_posts_source_type", table_name="bronze_posts")
    op.drop_index("idx_bronze_posts_external", table_name="bronze_posts")
    op.drop_index("idx_silver_posts_source_type", table_name="silver_posts")
    op.drop_index("idx_silver_posts_published", table_name="silver_posts")
    op.drop_index("idx_silver_posts_source_id", table_name="silver_posts")
    op.drop_index("idx_silver_posts_external", table_name="silver_posts")
    op.drop_index("idx_silver_posts_transcription", table_name="silver_posts")
    op.drop_index("idx_silver_comments_post_id", table_name="silver_comments")

    # ---- reddit_comments (was silver_comments) ----
    op.create_table(
        "reddit_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_item_id", sa.Integer(), sa.ForeignKey("content_items.id"), nullable=True),
        sa.Column("raw_comment_id", sa.Integer(), sa.ForeignKey("raw_comments.id"), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("parent_id", sa.Text(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=True),
        sa.UniqueConstraint("external_id"),
    )
    op.execute(
        "INSERT INTO reddit_comments "
        "(id, content_item_id, raw_comment_id, external_id, author, body, score, parent_id, depth, created_at) "
        "SELECT id, silver_post_id, bronze_comment_id, external_id, author, body, score, parent_id, depth, created_at "
        "FROM silver_comments"
    )
    op.drop_table("silver_comments")

    # ---- content_items (was silver_posts) ----
    op.create_table(
        "content_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("raw_item_id", sa.Integer(), sa.ForeignKey("raw_items.id"), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.Text(), server_default=sa.text("(datetime('now'))"), nullable=True),
        sa.Column("metadata", sa.Text(), nullable=True),
        sa.Column("transcription_status", sa.Text(), nullable=True),
        sa.Column("transcription_error", sa.Text(), nullable=True),
        sa.Column("detected_language", sa.Text(), nullable=True),
        sa.UniqueConstraint("source_type", "external_id"),
    )
    op.execute(
        "INSERT INTO content_items "
        "(id, source_id, raw_item_id, source_type, external_id, title, author, url, "
        "content_text, published_at, fetched_at, metadata, transcription_status, transcription_error, detected_language) "
        "SELECT id, source_id, bronze_post_id, source_type, external_id, title, author, url, "
        "content_text, published_at, fetched_at, meta, transcription_status, transcription_error, detected_language "
        "FROM silver_posts"
    )
    op.drop_table("silver_posts")

    # ---- raw_comments (was bronze_comments) ----
    op.create_table(
        "raw_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("raw_item_id", sa.Integer(), sa.ForeignKey("raw_items.id"), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("raw_data", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), server_default=sa.text("(datetime('now'))"), nullable=True),
        sa.UniqueConstraint("external_id"),
    )
    op.execute(
        "INSERT INTO raw_comments (id, raw_item_id, external_id, raw_data, fetched_at) "
        "SELECT id, bronze_post_id, external_id, raw_data, fetched_at FROM bronze_comments"
    )
    op.drop_table("bronze_comments")

    # ---- raw_items (was bronze_posts) ----
    op.rename_table("bronze_posts", "raw_items")

    # ---- Restore old indexes ----
    op.create_index("idx_raw_source_type", "raw_items", ["source_type"])
    op.create_index("idx_raw_external", "raw_items", ["source_type", "external_id"])
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

    op.execute("PRAGMA foreign_keys=ON")
