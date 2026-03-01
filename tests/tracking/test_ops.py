"""Unit tests for tracking ops (upsert_done, upsert_failed, upsert_skipped, retry_filter)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from aggre.tracking.model import StageTracking
from aggre.tracking.ops import retry_filter, upsert_done, upsert_failed, upsert_skipped
from aggre.tracking.status import COOLDOWN_SECONDS, MAX_RETRIES, Stage, StageStatus

pytestmark = pytest.mark.integration


def _get_tracking(engine: sa.engine.Engine, source: str, external_id: str, stage: Stage) -> sa.Row | None:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(StageTracking).where(
                StageTracking.source == source,
                StageTracking.external_id == external_id,
                StageTracking.stage == stage,
            )
        ).fetchone()


class TestUpsertDone:
    def test_creates_tracking_row(self, engine):
        upsert_done(engine, "content", "https://example.com/a", Stage.DOWNLOAD)

        row = _get_tracking(engine, "content", "https://example.com/a", Stage.DOWNLOAD)
        assert row is not None
        assert row.status == StageStatus.DONE
        assert row.completed_at is not None
        assert row.error is None

    def test_idempotent_re_upsert(self, engine):
        upsert_done(engine, "content", "https://example.com/b", Stage.DOWNLOAD)
        upsert_done(engine, "content", "https://example.com/b", Stage.DOWNLOAD)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/b",
                    StageTracking.stage == Stage.DOWNLOAD,
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0].status == StageStatus.DONE

    def test_transitions_from_skipped(self, engine):
        """upsert_done after skipped -> status becomes DONE."""
        upsert_skipped(engine, "content", "https://example.com/skip-then-done", Stage.DOWNLOAD, "pdf")
        upsert_done(engine, "content", "https://example.com/skip-then-done", Stage.DOWNLOAD)

        row = _get_tracking(engine, "content", "https://example.com/skip-then-done", Stage.DOWNLOAD)
        assert row is not None
        assert row.status == StageStatus.DONE
        assert row.error is None
        assert row.completed_at is not None

    def test_clears_error_on_success(self, engine):
        upsert_failed(engine, "content", "https://example.com/c", Stage.DOWNLOAD, "timeout")
        upsert_done(engine, "content", "https://example.com/c", Stage.DOWNLOAD)

        row = _get_tracking(engine, "content", "https://example.com/c", Stage.DOWNLOAD)
        assert row is not None
        assert row.status == StageStatus.DONE
        assert row.error is None
        assert row.completed_at is not None


class TestUpsertFailed:
    def test_creates_failed_row(self, engine):
        upsert_failed(engine, "content", "https://example.com/d", Stage.DOWNLOAD, "HTTP 500")

        row = _get_tracking(engine, "content", "https://example.com/d", Stage.DOWNLOAD)
        assert row is not None
        assert row.status == StageStatus.FAILED
        assert row.error == "HTTP 500"
        assert row.retries == 1

    def test_increments_retries(self, engine):
        upsert_failed(engine, "content", "https://example.com/e", Stage.DOWNLOAD, "err1")
        upsert_failed(engine, "content", "https://example.com/e", Stage.DOWNLOAD, "err2")
        upsert_failed(engine, "content", "https://example.com/e", Stage.DOWNLOAD, "err3")

        row = _get_tracking(engine, "content", "https://example.com/e", Stage.DOWNLOAD)
        assert row is not None
        assert row.retries == 3
        assert row.error == "err3"
        assert row.completed_at is None


class TestUpsertSkipped:
    def test_creates_skipped_row(self, engine):
        upsert_skipped(engine, "content", "https://example.com/f", Stage.DOWNLOAD, "pdf")

        row = _get_tracking(engine, "content", "https://example.com/f", Stage.DOWNLOAD)
        assert row is not None
        assert row.status == StageStatus.SKIPPED
        assert row.error == "pdf"
        assert row.completed_at is not None

    def test_idempotent_re_upsert(self, engine):
        """upsert_skipped called twice -> single row, still SKIPPED."""
        upsert_skipped(engine, "content", "https://example.com/skip2", Stage.DOWNLOAD, "pdf")
        upsert_skipped(engine, "content", "https://example.com/skip2", Stage.DOWNLOAD, "binary")

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/skip2",
                    StageTracking.stage == Stage.DOWNLOAD,
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0].status == StageStatus.SKIPPED
            assert rows[0].error == "binary"


class TestRetryFilter:
    def _insert_failed(self, engine: sa.engine.Engine, external_id: str, stage: Stage, *, retries: int, last_ran_at: str) -> None:
        """Insert a failed tracking row with explicit retries and last_ran_at."""
        with engine.begin() as conn:
            conn.execute(
                sa.insert(StageTracking).values(
                    source="content",
                    external_id=external_id,
                    stage=stage,
                    status=StageStatus.FAILED,
                    error="test error",
                    retries=retries,
                    last_ran_at=last_ran_at,
                )
            )

    def test_within_cooldown_excluded(self, engine):
        """Failed item within cooldown window is excluded from retry."""
        now = datetime.now(UTC).isoformat()
        self._insert_failed(engine, "url-recent", Stage.DOWNLOAD, retries=1, last_ran_at=now)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.external_id == "url-recent",
                    retry_filter(StageTracking, Stage.DOWNLOAD),
                )
            ).fetchall()
            assert len(rows) == 0

    def test_past_cooldown_included(self, engine):
        """Failed item past cooldown window is included in retry."""
        cooldown = COOLDOWN_SECONDS[Stage.DOWNLOAD]
        old_time = (datetime.now(UTC) - timedelta(seconds=cooldown + 60)).isoformat()
        self._insert_failed(engine, "url-old", Stage.DOWNLOAD, retries=1, last_ran_at=old_time)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.external_id == "url-old",
                    retry_filter(StageTracking, Stage.DOWNLOAD),
                )
            ).fetchall()
            assert len(rows) == 1

    def test_max_retries_excluded(self, engine):
        """Failed item at max retries is permanently excluded."""
        max_r = MAX_RETRIES[Stage.DOWNLOAD]
        cooldown = COOLDOWN_SECONDS[Stage.DOWNLOAD]
        old_time = (datetime.now(UTC) - timedelta(seconds=cooldown + 60)).isoformat()
        self._insert_failed(engine, "url-exhausted", Stage.DOWNLOAD, retries=max_r, last_ran_at=old_time)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.external_id == "url-exhausted",
                    retry_filter(StageTracking, Stage.DOWNLOAD),
                )
            ).fetchall()
            assert len(rows) == 0

    def test_excludes_done_and_skipped(self, engine):
        """retry_filter excludes rows with DONE or SKIPPED status."""
        cooldown = COOLDOWN_SECONDS[Stage.DOWNLOAD]
        old_time = (datetime.now(UTC) - timedelta(seconds=cooldown + 60)).isoformat()

        # Insert done row
        upsert_done(engine, "content", "url-done", Stage.DOWNLOAD)
        # Insert skipped row
        upsert_skipped(engine, "content", "url-skipped", Stage.DOWNLOAD, "pdf")
        # Insert failed row (should be the only one matched)
        self._insert_failed(engine, "url-retry", Stage.DOWNLOAD, retries=1, last_ran_at=old_time)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(StageTracking).where(
                    retry_filter(StageTracking, Stage.DOWNLOAD),
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0].external_id == "url-retry"

    def test_works_with_aliased_model(self, engine):
        """retry_filter works with sa.orm.aliased(StageTracking)."""
        import sqlalchemy.orm

        cooldown = COOLDOWN_SECONDS[Stage.EXTRACT]
        old_time = (datetime.now(UTC) - timedelta(seconds=cooldown + 60)).isoformat()
        self._insert_failed(engine, "url-aliased", Stage.EXTRACT, retries=1, last_ran_at=old_time)

        st_alias = sqlalchemy.orm.aliased(StageTracking)
        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(st_alias).where(
                    st_alias.external_id == "url-aliased",
                    retry_filter(st_alias, Stage.EXTRACT),
                )
            ).fetchall()
            assert len(rows) == 1
