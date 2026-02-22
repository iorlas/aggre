"""Base collector with shared helpers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import sqlalchemy as sa
import structlog
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.db import SilverDiscussion, Source, now_iso
from aggre.settings import Settings
from aggre.statuses import CommentsStatus
from aggre.utils.bronze import DEFAULT_BRONZE_ROOT, write_bronze_json


class Collector(Protocol):
    """Protocol that all collectors must implement."""

    def collect(self, engine: sa.engine.Engine, config: BaseModel, settings: Settings, log: structlog.stdlib.BoundLogger) -> int:
        """Fetch new items from the source. Returns count of new items stored."""
        ...


class SearchableCollector(Collector, Protocol):
    """Collector that supports searching for discussions by URL."""

    def search_by_url(
        self, url: str, engine: sa.engine.Engine, config: BaseModel, settings: Settings, log: structlog.stdlib.BoundLogger
    ) -> int:
        """Search for discussions about a URL. Returns count of new items stored."""
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
        """Return discussions with pending comments for this source_type."""
        with engine.connect() as conn:
            return conn.execute(
                sa.select(SilverDiscussion.id, SilverDiscussion.external_id, SilverDiscussion.meta)
                .where(
                    SilverDiscussion.source_type == self.source_type,
                    SilverDiscussion.comments_status == CommentsStatus.PENDING,
                )
                .limit(batch_limit)
            ).fetchall()

    def _mark_comments_done(
        self,
        engine: sa.engine.Engine,
        discussion_id: int,
        comments_json: str | None,
        comment_count: int,
    ) -> None:
        """PENDING → DONE. Stores fetched comments."""
        with engine.begin() as conn:
            conn.execute(
                sa.update(SilverDiscussion)
                .where(SilverDiscussion.id == discussion_id)
                .values(
                    comments_status=CommentsStatus.DONE,
                    comments_json=comments_json,
                    comment_count=comment_count,
                )
            )

    @staticmethod
    def _upsert_discussion(
        conn: sa.Connection,
        values: dict[str, object],
        update_columns: Sequence[str] | None = None,
    ) -> int | None:
        """Insert or update a SilverDiscussion. Returns id if new, None if existing."""
        # Check existence first so we can distinguish insert from update
        existing = conn.execute(
            sa.select(SilverDiscussion.id).where(
                SilverDiscussion.source_type == values["source_type"],
                SilverDiscussion.external_id == values["external_id"],
            )
        ).first()

        stmt = pg_insert(SilverDiscussion).values(**values)
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
            sa.select(SilverDiscussion.id).where(
                SilverDiscussion.source_type == values["source_type"],
                SilverDiscussion.external_id == values["external_id"],
            )
        ).scalar()
