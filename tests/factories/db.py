from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.db import SilverContent, SilverDiscussion, Source

__all__ = ["seed_content", "seed_discussion", "seed_source"]


def seed_content(
    engine: sa.engine.Engine,
    url: str,
    *,
    domain: str | None = None,
    text: str | None = None,
    original_url: str | None = None,
) -> int:
    """Insert a SilverContent row. Returns the row id."""
    with engine.begin() as conn:
        stmt = pg_insert(SilverContent).values(
            canonical_url=url,
            domain=domain,
            text=text,
            original_url=original_url,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
        result = conn.execute(stmt)
        return result.inserted_primary_key[0]


def seed_discussion(
    engine: sa.engine.Engine,
    *,
    source_type: str,
    external_id: str,
    content_id: int | None = None,
    title: str | None = None,
    author: str | None = None,
    url: str | None = None,
    content_text: str | None = None,
    published_at: str | None = None,
    comments_json: str | None = None,
    score: int | None = None,
    comment_count: int | None = None,
    source_id: int | None = None,
    meta: str | None = None,
) -> int:
    """Insert a SilverDiscussion row. Returns the row id."""
    with engine.begin() as conn:
        stmt = pg_insert(SilverDiscussion).values(
            source_type=source_type,
            external_id=external_id,
            content_id=content_id,
            title=title,
            author=author,
            url=url,
            content_text=content_text,
            published_at=published_at,
            comments_json=comments_json,
            score=score,
            comment_count=comment_count,
            source_id=source_id,
            meta=meta,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["source_type", "external_id"])
        result = conn.execute(stmt)
        return result.inserted_primary_key[0]


def seed_source(
    engine: sa.engine.Engine,
    *,
    source_type: str,
    name: str,
    config: str = "{}",
) -> int:
    """Insert a Source row. Returns the row id."""
    with engine.begin() as conn:
        result = conn.execute(sa.insert(Source).values(type=source_type, name=name, config=config))
        return result.inserted_primary_key[0]
