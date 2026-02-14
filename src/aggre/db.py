"""Database engine, table definitions, and connection management."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import event

metadata = sa.MetaData()

sources = sa.Table(
    "sources",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("config", sa.Text, nullable=False),
    sa.Column("enabled", sa.Integer, server_default="1"),
    sa.Column("created_at", sa.Text, server_default=sa.text("(datetime('now'))")),
    sa.Column("last_fetched_at", sa.Text, nullable=True),
)

raw_items = sa.Table(
    "raw_items",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("source_type", sa.Text, nullable=False),
    sa.Column("external_id", sa.Text, nullable=False),
    sa.Column("raw_data", sa.Text, nullable=False),
    sa.Column("fetched_at", sa.Text, server_default=sa.text("(datetime('now'))")),
    sa.UniqueConstraint("source_type", "external_id"),
)

content_items = sa.Table(
    "content_items",
    metadata,
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

raw_comments = sa.Table(
    "raw_comments",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("raw_item_id", sa.Integer, sa.ForeignKey("raw_items.id"), nullable=True),
    sa.Column("external_id", sa.Text, nullable=False),
    sa.Column("raw_data", sa.Text, nullable=False),
    sa.Column("fetched_at", sa.Text, server_default=sa.text("(datetime('now'))")),
    sa.UniqueConstraint("external_id"),
)

reddit_comments = sa.Table(
    "reddit_comments",
    metadata,
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

# Indexes
sa.Index("idx_raw_source_type", raw_items.c.source_type)
sa.Index("idx_raw_external", raw_items.c.source_type, raw_items.c.external_id)
sa.Index("idx_content_source_type", content_items.c.source_type)
sa.Index("idx_content_published", content_items.c.published_at)
sa.Index("idx_content_source_id", content_items.c.source_id)
sa.Index("idx_content_external", content_items.c.source_type, content_items.c.external_id)
sa.Index(
    "idx_content_transcription",
    content_items.c.transcription_status,
    sqlite_where=content_items.c.transcription_status.isnot(None),
)
sa.Index("idx_comments_content_item", reddit_comments.c.content_item_id)


def _set_sqlite_pragmas(dbapi_conn, connection_record):  # noqa: N802
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_engine(db_path: str) -> sa.engine.Engine:
    """Create a SQLAlchemy engine for the given SQLite database path."""
    engine = sa.create_engine(f"sqlite:///{db_path}", echo=False)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine
