"""Base collector with shared helpers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, TypedDict

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.db import SilverContent, SilverObservation, Source
from aggre.settings import Settings
from aggre.stages.model import StageTracking
from aggre.stages.status import Stage
from aggre.stages.tracking import retry_filter, upsert_done, upsert_failed
from aggre.urls import normalize_url
from aggre.utils.bronze import DEFAULT_BRONZE_ROOT, write_bronze_json
from aggre.utils.db import now_iso
from aggre.utils.urls import extract_domain


class ContentReference(TypedDict):
    """A reference to a piece of content from a collector feed."""

    external_id: str
    raw_data: dict[str, object]
    source_id: int


class Collector(Protocol):
    """Protocol that all collectors must implement."""

    source_type: str

    def collect_references(self, engine: sa.engine.Engine, config: BaseModel, settings: Settings) -> list[ContentReference]:
        """Fetch feed, write each item to bronze, return references.

        Handles source management (ensure_source, TTL, fetch limits).
        Writes raw data to bronze. Returns refs with source_ids for process_reference.
        """
        ...

    def process_reference(self, ref_data: dict[str, object], conn: sa.Connection, source_id: int) -> None:
        """Normalize one bronze reference into silver rows.

        Calls ensure_content() + _upsert_observation().
        For self-posts: also populates SilverContent.text directly.
        """
        ...


class SearchableCollector(Collector, Protocol):
    """Collector that supports searching for observations by URL."""

    def search_by_url(self, url: str, engine: sa.engine.Engine, config: BaseModel, settings: Settings) -> int:
        """Search for observations about a URL. Returns count of new items stored."""
        ...


class BaseCollector:
    """Shared helpers for all collectors."""

    source_type: str

    def _ensure_source(self, engine: sa.engine.Engine, name: str, source_config: dict[str, object] | None = None) -> int:
        """Find or create a Source row. Returns source_id."""
        with engine.begin() as conn:
            row = conn.execute(sa.select(Source.id).where(Source.type == self.source_type, Source.name == name)).first()
            if row:
                return row[0]
            cfg = json.dumps(source_config or {"name": name})
            result = conn.execute(sa.insert(Source).values(type=self.source_type, name=name, config=cfg))
            return result.inserted_primary_key[0]

    def _write_bronze(self, external_id: str, raw_data: object, *, bronze_root: Path = DEFAULT_BRONZE_ROOT) -> Path:
        """Write raw item data to bronze filesystem."""
        return write_bronze_json(self.source_type, external_id, raw_data, bronze_root=bronze_root)

    def _update_last_fetched(self, engine: sa.engine.Engine, source_id: int) -> None:
        """Update the last_fetched_at timestamp on a Source."""
        with engine.begin() as conn:
            conn.execute(sa.update(Source).where(Source.id == source_id).values(last_fetched_at=now_iso()))

    def _is_initialized(self, engine: sa.engine.Engine, source_id: int) -> bool:
        """True if source has been fetched at least once."""
        with engine.connect() as conn:
            last = conn.execute(sa.select(Source.last_fetched_at).where(Source.id == source_id)).scalar()
        return last is not None

    def _get_fetch_limit(self, engine: sa.engine.Engine, source_id: int, init_limit: int, normal_limit: int) -> int:
        """Return init_limit on first-ever fetch, normal_limit otherwise."""
        return normal_limit if self._is_initialized(engine, source_id) else init_limit

    def _is_source_recent(self, engine: sa.engine.Engine, source_id: int, ttl_minutes: int) -> bool:
        """True if this source was fetched within ttl_minutes.

        Returns False when ttl_minutes is 0 (disabled), source was never fetched, or is stale.
        """
        if ttl_minutes <= 0:
            return False
        cutoff = (datetime.now(UTC) - timedelta(minutes=ttl_minutes)).isoformat()
        with engine.connect() as conn:
            last = conn.execute(sa.select(Source.last_fetched_at).where(Source.id == source_id)).scalar()
        if last is None:
            return False
        return last >= cutoff

    def _query_pending_comments(self, engine: sa.engine.Engine, batch_limit: int) -> list[sa.Row]:
        """Return observations with pending comments for this source_type."""
        with engine.connect() as conn:
            return conn.execute(
                sa.select(SilverObservation.id, SilverObservation.external_id, SilverObservation.meta)
                .outerjoin(
                    StageTracking,
                    sa.and_(
                        StageTracking.source == self.source_type,
                        StageTracking.external_id == SilverObservation.external_id,
                        StageTracking.stage == Stage.COMMENTS,
                    ),
                )
                .where(
                    SilverObservation.source_type == self.source_type,
                    SilverObservation.comments_json.is_(None),
                    sa.or_(
                        StageTracking.id.is_(None),
                        retry_filter(StageTracking, Stage.COMMENTS),
                    ),
                )
                .limit(batch_limit)
            ).fetchall()

    def _mark_comments_done(
        self,
        engine: sa.engine.Engine,
        observation_id: int,
        external_id: str,
        comments_json: str | None,
        comment_count: int,
    ) -> None:
        """Store fetched comments on an observation and record tracking."""
        with engine.begin() as conn:
            conn.execute(
                sa.update(SilverObservation)
                .where(SilverObservation.id == observation_id)
                .values(
                    comments_json=comments_json,
                    comment_count=comment_count,
                )
            )
        upsert_done(engine, self.source_type, external_id, Stage.COMMENTS)

    def _mark_comments_failed(
        self,
        engine: sa.engine.Engine,
        external_id: str,
        error: str,
    ) -> None:
        """Record comments fetch failure in tracking."""
        upsert_failed(engine, self.source_type, external_id, Stage.COMMENTS, error)

    @staticmethod
    def _upsert_observation(
        conn: sa.Connection,
        values: dict[str, object],
        update_columns: Sequence[str] | None = None,
    ) -> int | None:
        """Insert or update a SilverObservation. Returns id if new, None if existing."""
        # Check existence first so we can distinguish insert from update
        existing = conn.execute(
            sa.select(SilverObservation.id).where(
                SilverObservation.source_type == values["source_type"],
                SilverObservation.external_id == values["external_id"],
            )
        ).first()

        stmt = pg_insert(SilverObservation).values(**values)
        if update_columns:
            set_ = {col: getattr(stmt.excluded, col) for col in update_columns}
            stmt = stmt.on_conflict_do_update(
                index_elements=["source_type", "external_id"],
                set_=set_,
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=["source_type", "external_id"])
        conn.execute(stmt)

        if existing:
            return None
        return conn.execute(
            sa.select(SilverObservation.id).where(
                SilverObservation.source_type == values["source_type"],
                SilverObservation.external_id == values["external_id"],
            )
        ).scalar()

    @staticmethod
    def _ensure_self_post_content(conn: sa.Connection, discussion_url: str, text: str) -> int | None:
        """Create a SilverContent row for a self-post with text populated immediately.

        The content pipeline will skip this row because text is already set (null-check pattern).
        Returns the content_id, or None if text is empty.
        """
        if not text:
            return None

        canonical = normalize_url(discussion_url)
        if not canonical:
            return None

        row = conn.execute(sa.select(SilverContent.id).where(SilverContent.canonical_url == canonical)).first()
        if row:
            return row[0]

        domain = extract_domain(canonical)
        stmt = pg_insert(SilverContent).values(
            canonical_url=canonical,
            domain=domain,
            text=text,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
        result = conn.execute(stmt)
        if result.rowcount == 0:
            row = conn.execute(sa.select(SilverContent.id).where(SilverContent.canonical_url == canonical)).first()
            return row[0] if row else None
        return result.inserted_primary_key[0]
