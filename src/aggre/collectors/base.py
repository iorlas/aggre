"""Base collector with shared helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol, Sequence

import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.config import AppConfig
from aggre.db import BronzeDiscussion, SilverDiscussion, Source


class Collector(Protocol):
    """Protocol that all collectors must implement."""

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        """Fetch new items from the source. Returns count of new items stored."""
        ...


class SearchableCollector(Collector, Protocol):
    """Collector that supports searching for discussions by URL."""

    def search_by_url(self, url: str, engine: sa.engine.Engine, config: AppConfig,
                      log: structlog.stdlib.BoundLogger) -> int:
        """Search for discussions about a URL. Returns count of new items stored."""
        ...


class BaseCollector:
    """Shared helpers for all collectors."""

    source_type: str

    def _ensure_source(self, engine: sa.engine.Engine, name: str, source_config: dict[str, Any] | None = None) -> int:
        """Find or create a Source row. Returns source_id."""
        with engine.begin() as conn:
            row = conn.execute(
                sa.select(Source.id).where(Source.type == self.source_type, Source.name == name)
            ).first()
            if row:
                return row[0]
            cfg = json.dumps(source_config or {"name": name})
            result = conn.execute(
                sa.insert(Source).values(type=self.source_type, name=name, config=cfg)
            )
            return result.inserted_primary_key[0]

    def _store_raw_item(self, conn: sa.Connection, ext_id: str, raw_data: Any) -> int | None:
        """Insert a BronzeDiscussion. Returns id if new, None if duplicate."""
        stmt = pg_insert(BronzeDiscussion).values(
            source_type=self.source_type,
            external_id=ext_id,
            raw_data=json.dumps(raw_data) if not isinstance(raw_data, str) else raw_data,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["source_type", "external_id"])
        result = conn.execute(stmt)
        if result.rowcount == 0:
            return None
        return result.inserted_primary_key[0]

    def _update_last_fetched(self, engine: sa.engine.Engine, source_id: int) -> None:
        """Update the last_fetched_at timestamp on a Source."""
        with engine.begin() as conn:
            conn.execute(
                sa.update(Source).where(Source.id == source_id)
                .values(last_fetched_at=datetime.now(UTC).isoformat())
            )

    @staticmethod
    def _upsert_discussion(
        conn: sa.Connection,
        values: dict[str, Any],
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
