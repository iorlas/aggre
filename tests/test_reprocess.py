"""Tests for reprocess_from_bronze — rebuild silver from bronze without external APIs."""

from __future__ import annotations

import json
import logging

import pytest
import sqlalchemy as sa

from aggre.dagster_defs.reprocess.job import reprocess_from_bronze
from aggre.db import SilverDiscussion, Source
from tests.factories import hn_hit, lobsters_story

pytestmark = pytest.mark.integration


class TestReprocessFromBronze:
    def test_empty_bronze_returns_zero(self, engine, tmp_bronze):
        """No raw.json files -> returns 0."""
        count = reprocess_from_bronze(engine, bronze_root=tmp_bronze)
        assert count == 0

    def test_reprocesses_single_source_type(self, engine, tmp_bronze):
        """Write HN raw.json, verify SilverDiscussion created."""
        hn_dir = tmp_bronze / "hackernews" / "12345"
        hn_dir.mkdir(parents=True)

        raw_data = hn_hit(object_id="12345", title="Test Story", url="https://example.com/article")
        (hn_dir / "raw.json").write_text(json.dumps(raw_data))

        count = reprocess_from_bronze(engine, bronze_root=tmp_bronze)
        assert count == 1

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(rows) == 1
            assert rows[0].source_type == "hackernews"
            assert rows[0].external_id == "12345"
            assert rows[0].title == "Test Story"
            assert rows[0].url == "https://example.com/article"

    def test_reprocesses_multiple_source_types(self, engine, tmp_bronze):
        """Write raw.json for HN + Lobsters, verify both processed."""
        # HN bronze
        hn_dir = tmp_bronze / "hackernews" / "111"
        hn_dir.mkdir(parents=True)
        (hn_dir / "raw.json").write_text(json.dumps(hn_hit(object_id="111", title="HN Story")))

        # Lobsters bronze
        lob_dir = tmp_bronze / "lobsters" / "abc123"
        lob_dir.mkdir(parents=True)
        (lob_dir / "raw.json").write_text(json.dumps(lobsters_story(short_id="abc123", title="Lobsters Story")))

        count = reprocess_from_bronze(engine, bronze_root=tmp_bronze)
        assert count == 2

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion).order_by(SilverDiscussion.source_type)).fetchall()
            assert len(rows) == 2

            source_types = {r.source_type for r in rows}
            assert source_types == {"hackernews", "lobsters"}

            titles = {r.title for r in rows}
            assert "HN Story" in titles
            assert "Lobsters Story" in titles

    def test_skips_invalid_json(self, engine, tmp_bronze, caplog):
        """Invalid JSON in raw.json is skipped with error logged."""
        hn_dir = tmp_bronze / "hackernews" / "bad"
        hn_dir.mkdir(parents=True)
        (hn_dir / "raw.json").write_text("{not valid json!!!")

        with caplog.at_level(logging.ERROR):
            count = reprocess_from_bronze(engine, bronze_root=tmp_bronze)

        assert count == 0
        assert any("reprocess.ref_error" in r.message for r in caplog.records)

    def test_error_in_one_ref_continues(self, engine, tmp_bronze, caplog):
        """One bad raw.json does not stop processing of other files."""
        # Bad file
        bad_dir = tmp_bronze / "hackernews" / "bad"
        bad_dir.mkdir(parents=True)
        (bad_dir / "raw.json").write_text("{invalid json")

        # Good file
        good_dir = tmp_bronze / "hackernews" / "good"
        good_dir.mkdir(parents=True)
        (good_dir / "raw.json").write_text(json.dumps(hn_hit(object_id="good", title="Good Story")))

        with caplog.at_level(logging.ERROR):
            count = reprocess_from_bronze(engine, bronze_root=tmp_bronze)

        assert count == 1

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(rows) == 1
            assert rows[0].external_id == "good"

        # The bad file should have been logged
        assert any("reprocess.ref_error" in r.message for r in caplog.records)

    def test_creates_source_if_missing(self, engine, tmp_bronze):
        """Source row is created if it does not exist."""
        # Verify no sources exist before reprocessing
        with engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(Source)).scalar() == 0

        hn_dir = tmp_bronze / "hackernews" / "42"
        hn_dir.mkdir(parents=True)
        (hn_dir / "raw.json").write_text(json.dumps(hn_hit(object_id="42")))

        reprocess_from_bronze(engine, bronze_root=tmp_bronze)

        with engine.connect() as conn:
            sources = conn.execute(sa.select(Source)).fetchall()
            assert len(sources) == 1
            assert sources[0].type == "hackernews"

            # Discussion should reference the created source
            obs = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert obs.source_id == sources[0].id
