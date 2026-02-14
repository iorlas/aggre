"""Database engine, ORM models, and connection management."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    config: Mapped[str] = mapped_column(sa.Text, nullable=False)
    enabled: Mapped[int | None] = mapped_column(sa.Integer, server_default="1")
    created_at: Mapped[str | None] = mapped_column(sa.Text, server_default=sa.text("(datetime('now'))"))
    last_fetched_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class BronzePost(Base):
    __tablename__ = "bronze_posts"
    __table_args__ = (sa.UniqueConstraint("source_type", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    raw_data: Mapped[str] = mapped_column(sa.Text, nullable=False)
    fetched_at: Mapped[str | None] = mapped_column(sa.Text, server_default=sa.text("(datetime('now'))"))


class BronzeComment(Base):
    __tablename__ = "bronze_comments"
    __table_args__ = (sa.UniqueConstraint("external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bronze_post_id: Mapped[int | None] = mapped_column(sa.ForeignKey("bronze_posts.id"), nullable=True)
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    raw_data: Mapped[str] = mapped_column(sa.Text, nullable=False)
    fetched_at: Mapped[str | None] = mapped_column(sa.Text, server_default=sa.text("(datetime('now'))"))


class SilverPost(Base):
    __tablename__ = "silver_posts"
    __table_args__ = (sa.UniqueConstraint("source_type", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(sa.ForeignKey("sources.id"), nullable=True)
    bronze_post_id: Mapped[int | None] = mapped_column(sa.ForeignKey("bronze_posts.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    author: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    published_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(sa.Text, server_default=sa.text("(datetime('now'))"))
    meta: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    transcription_status: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    transcription_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class SilverComment(Base):
    __tablename__ = "silver_comments"
    __table_args__ = (sa.UniqueConstraint("external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    silver_post_id: Mapped[int | None] = mapped_column(sa.ForeignKey("silver_posts.id"), nullable=True)
    bronze_comment_id: Mapped[int | None] = mapped_column(sa.ForeignKey("bronze_comments.id"), nullable=True)
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    author: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    body: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    depth: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


# Bronze indexes
sa.Index("idx_bronze_posts_source_type", BronzePost.source_type)
sa.Index("idx_bronze_posts_external", BronzePost.source_type, BronzePost.external_id)

# Silver indexes
sa.Index("idx_silver_posts_source_type", SilverPost.source_type)
sa.Index("idx_silver_posts_published", SilverPost.published_at)
sa.Index("idx_silver_posts_source_id", SilverPost.source_id)
sa.Index("idx_silver_posts_external", SilverPost.source_type, SilverPost.external_id)
sa.Index(
    "idx_silver_posts_transcription",
    SilverPost.transcription_status,
    sqlite_where=SilverPost.transcription_status.isnot(None),
)
sa.Index("idx_silver_posts_url", SilverPost.url, sqlite_where=SilverPost.url.isnot(None))
sa.Index("idx_silver_comments_post_id", SilverComment.silver_post_id)


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
