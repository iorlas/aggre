"""Tests for Dagster sensors — concurrency guards and null-check queries."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import dagster as dg
import pytest
import sqlalchemy as sa

from aggre.dagster_defs.comments.sensor import comments_sensor
from aggre.dagster_defs.content.sensor import content_sensor
from aggre.dagster_defs.enrichment.sensor import enrichment_sensor
from aggre.dagster_defs.resources import DatabaseResource
from aggre.dagster_defs.transcription.sensor import transcription_sensor
from aggre.db import SilverContent, SilverObservation

pytestmark = pytest.mark.integration


def _sensor_context(*, cursor: str | None = None, active_runs: list[Any] | None = None) -> MagicMock:
    """Mock SensorEvaluationContext with controllable instance.get_runs."""
    ctx = MagicMock()
    ctx.cursor = cursor
    ctx.instance.get_runs.return_value = active_runs or []
    return ctx


def _database(engine: sa.engine.Engine) -> MagicMock:
    """Mock DatabaseResource wrapping real test engine."""
    db = MagicMock(spec=DatabaseResource)
    db.get_engine.return_value = engine
    return db


def _run_sensor(sensor_def: dg.SensorDefinition, ctx: MagicMock, db: MagicMock) -> dg.SensorResult:
    """Call the raw sensor function directly, bypassing Dagster's invocation machinery.

    We use ``_raw_fn`` because Dagster's ``build_sensor_context`` / ``execute_sensor``
    tries to resolve ``DatabaseResource`` via the resource system, which doesn't work
    with our mock.  Calling the underlying function directly is simpler and sufficient
    for testing query logic + concurrency guards.  If Dagster renames this internal,
    only this helper needs updating.
    """
    result = sensor_def._raw_fn(context=ctx, database=db)  # noqa: SLF001
    assert isinstance(result, dg.SensorResult)
    return result


def _skip_message(result: dg.SensorResult) -> str:
    """Extract skip message string from SensorResult."""
    assert result.skip_reason is not None
    msg = result.skip_reason.skip_message
    assert msg is not None
    return msg


# ---------------------------------------------------------------------------
# content_sensor
# ---------------------------------------------------------------------------


class TestContentSensor:
    def test_skips_when_job_running(self, engine):
        ctx = _sensor_context(active_runs=[MagicMock()])
        result = _run_sensor(content_sensor, ctx, _database(engine))
        assert "already running" in _skip_message(result)
        assert not result.run_requests
        assert ctx.instance.get_runs.call_args.kwargs["filters"].job_name == "content_job"

    def test_triggers_when_work_pending(self, engine):
        with engine.begin() as conn:
            conn.execute(sa.insert(SilverContent).values(canonical_url="https://example.com", domain="example.com"))
        ctx = _sensor_context()
        result = _run_sensor(content_sensor, ctx, _database(engine))
        assert result.run_requests and len(result.run_requests) == 1

    def test_skips_when_no_work(self, engine):
        ctx = _sensor_context()
        result = _run_sensor(content_sensor, ctx, _database(engine))
        assert "No content" in _skip_message(result)

    def test_skips_youtube_domain_rows(self, engine):
        with engine.begin() as conn:
            conn.execute(
                sa.insert(SilverContent).values(
                    canonical_url="https://youtube.com/watch?v=abc",
                    domain="youtube.com",
                )
            )
        ctx = _sensor_context()
        result = _run_sensor(content_sensor, ctx, _database(engine))
        assert "No content" in _skip_message(result)


# ---------------------------------------------------------------------------
# transcription_sensor
# ---------------------------------------------------------------------------


class TestTranscriptionSensor:
    def test_skips_when_job_running(self, engine):
        ctx = _sensor_context(active_runs=[MagicMock()])
        result = _run_sensor(transcription_sensor, ctx, _database(engine))
        assert "already running" in _skip_message(result)
        assert not result.run_requests
        assert ctx.instance.get_runs.call_args.kwargs["filters"].job_name == "transcribe_job"

    def test_triggers_when_youtube_needs_transcription(self, engine):
        with engine.begin() as conn:
            content_id = conn.execute(
                sa.insert(SilverContent)
                .values(
                    canonical_url="https://youtube.com/watch?v=abc",
                    domain="youtube.com",
                )
                .returning(SilverContent.id)
            ).scalar()
            conn.execute(
                sa.insert(SilverObservation).values(
                    source_type="youtube",
                    external_id="abc",
                    content_id=content_id,
                )
            )
        ctx = _sensor_context()
        result = _run_sensor(transcription_sensor, ctx, _database(engine))
        assert result.run_requests and len(result.run_requests) == 1

    def test_skips_when_no_work(self, engine):
        ctx = _sensor_context()
        result = _run_sensor(transcription_sensor, ctx, _database(engine))
        assert "No videos" in _skip_message(result)

    def test_requires_youtube_source_type(self, engine):
        """Non-youtube observations with unprocessed content should not trigger."""
        with engine.begin() as conn:
            content_id = conn.execute(
                sa.insert(SilverContent)
                .values(
                    canonical_url="https://example.com/article",
                    domain="example.com",
                )
                .returning(SilverContent.id)
            ).scalar()
            conn.execute(
                sa.insert(SilverObservation).values(
                    source_type="hackernews",
                    external_id="12345",
                    content_id=content_id,
                )
            )
        ctx = _sensor_context()
        result = _run_sensor(transcription_sensor, ctx, _database(engine))
        assert "No videos" in _skip_message(result)


# ---------------------------------------------------------------------------
# enrichment_sensor
# ---------------------------------------------------------------------------


class TestEnrichmentSensor:
    def test_skips_when_job_running(self, engine):
        ctx = _sensor_context(active_runs=[MagicMock()])
        result = _run_sensor(enrichment_sensor, ctx, _database(engine))
        assert "already running" in _skip_message(result)
        assert not result.run_requests
        assert ctx.instance.get_runs.call_args.kwargs["filters"].job_name == "enrich_job"

    def test_triggers_when_text_set_but_not_enriched(self, engine):
        with engine.begin() as conn:
            conn.execute(
                sa.insert(SilverContent).values(
                    canonical_url="https://example.com",
                    domain="example.com",
                    text="Some article text",
                )
            )
        ctx = _sensor_context()
        result = _run_sensor(enrichment_sensor, ctx, _database(engine))
        assert result.run_requests and len(result.run_requests) == 1

    def test_skips_when_no_work(self, engine):
        ctx = _sensor_context()
        result = _run_sensor(enrichment_sensor, ctx, _database(engine))
        assert "No content" in _skip_message(result)

    def test_skips_already_enriched_content(self, engine):
        """Content with text but already enriched should not trigger."""
        with engine.begin() as conn:
            conn.execute(
                sa.insert(SilverContent).values(
                    canonical_url="https://example.com",
                    domain="example.com",
                    text="Some text",
                    enriched_at="2024-01-01",
                )
            )
        ctx = _sensor_context()
        result = _run_sensor(enrichment_sensor, ctx, _database(engine))
        assert "No content" in _skip_message(result)


# ---------------------------------------------------------------------------
# comments_sensor
# ---------------------------------------------------------------------------


class TestCommentsSensor:
    def test_skips_when_job_running(self, engine):
        ctx = _sensor_context(active_runs=[MagicMock()])
        result = _run_sensor(comments_sensor, ctx, _database(engine))
        assert "already running" in _skip_message(result)
        assert not result.run_requests
        assert ctx.instance.get_runs.call_args.kwargs["filters"].job_name == "comments_job"

    def test_triggers_when_pending_comments(self, engine):
        with engine.begin() as conn:
            conn.execute(
                sa.insert(SilverObservation).values(
                    source_type="hackernews",
                    external_id="12345",
                )
            )
        ctx = _sensor_context()
        result = _run_sensor(comments_sensor, ctx, _database(engine))
        assert result.run_requests and len(result.run_requests) == 1

    def test_skips_when_no_work(self, engine):
        ctx = _sensor_context()
        result = _run_sensor(comments_sensor, ctx, _database(engine))
        assert "No observations" in _skip_message(result)

    def test_only_counts_comment_source_types(self, engine):
        """Source types outside reddit/hackernews/lobsters should not trigger."""
        with engine.begin() as conn:
            conn.execute(
                sa.insert(SilverObservation).values(
                    source_type="youtube",
                    external_id="vid1",
                )
            )
        ctx = _sensor_context()
        result = _run_sensor(comments_sensor, ctx, _database(engine))
        assert "No observations" in _skip_message(result)
