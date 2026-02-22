"""ORM models, indexes, and content update helper."""

from __future__ import annotations

import sqlalchemy as sa
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
    created_at: Mapped[str | None] = mapped_column(sa.Text, server_default=sa.func.now())
    last_fetched_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class SilverContent(Base):
    __tablename__ = "silver_content"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_url: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    fetch_status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="pending")
    fetch_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(sa.Text, server_default=sa.func.now())
    # Transcription fields (content-level concern)
    transcription_status: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    transcription_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    # Enrichment tracking
    enriched_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class SilverDiscussion(Base):
    __tablename__ = "silver_discussions"
    __table_args__ = (sa.UniqueConstraint("source_type", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(sa.ForeignKey("sources.id"), nullable=True)
    content_id: Mapped[int | None] = mapped_column(sa.ForeignKey("silver_content.id"), nullable=True)
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    author: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    url: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    published_at: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    fetched_at: Mapped[str | None] = mapped_column(sa.Text, server_default=sa.func.now())
    meta: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    comments_status: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    comments_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)


# SilverContent indexes
sa.Index("idx_silver_content_domain", SilverContent.domain, postgresql_where=SilverContent.domain.isnot(None))
sa.Index("idx_silver_content_fetch_status", SilverContent.fetch_status)
sa.Index(
    "idx_silver_content_transcription",
    SilverContent.transcription_status,
    postgresql_where=SilverContent.transcription_status.isnot(None),
)
sa.Index("idx_silver_content_enriched_at", SilverContent.enriched_at, postgresql_where=SilverContent.enriched_at.is_(None))

# SilverDiscussion indexes
sa.Index("idx_silver_discussions_source_type", SilverDiscussion.source_type)
sa.Index("idx_silver_discussions_published", SilverDiscussion.published_at)
sa.Index("idx_silver_discussions_source_id", SilverDiscussion.source_id)
sa.Index("idx_silver_discussions_external", SilverDiscussion.source_type, SilverDiscussion.external_id)
sa.Index(
    "idx_silver_discussions_comments_status",
    SilverDiscussion.comments_status,
    postgresql_where=SilverDiscussion.comments_status.isnot(None),
)
sa.Index("idx_silver_discussions_url", SilverDiscussion.url, postgresql_where=SilverDiscussion.url.isnot(None))
sa.Index("idx_silver_discussions_content_id", SilverDiscussion.content_id, postgresql_where=SilverDiscussion.content_id.isnot(None))


def update_content(engine: sa.engine.Engine, content_id: int, **values: str | int | None) -> None:
    """Update a SilverContent row by id in its own transaction."""
    with engine.begin() as conn:
        conn.execute(sa.update(SilverContent).where(SilverContent.id == content_id).values(**values))
