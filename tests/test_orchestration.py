"""Tests for collection and comments orchestration ops.

All dependencies (config, collectors, logging, engine) are mocked — no database
or external services required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aggre.dagster_defs.collection._shared import collect_source
from aggre.dagster_defs.comments.job import fetch_comments as _comments_op
from tests.factories import make_config

pytestmark = pytest.mark.integration

# Extract the raw Python function from the Dagster @op wrapper so we can call
# it directly with a MagicMock context, bypassing Dagster's invocation validation.
fetch_comments = _comments_op.compute_fn.decorated_fn  # type: ignore[union-attr]


# Override session-scoped fixtures from conftest.py so these tests run without PostgreSQL.
@pytest.fixture()
def engine():
    """No-op engine override — orchestration tests use mocks, not a real DB."""
    return MagicMock()


@pytest.fixture(autouse=True)
def clean_tables():
    """No-op override of the autouse table-truncation fixture."""
    yield


def _mock_context(engine: object, config: object | None = None) -> MagicMock:
    """Create a mock Dagster OpExecutionContext with database and app_config resources."""
    ctx = MagicMock()
    ctx.resources.database.get_engine.return_value = engine
    if config is not None:
        ctx.resources.app_config.get_config.return_value = config
    return ctx


# ---------------------------------------------------------------------------
# collect_source
# ---------------------------------------------------------------------------


class TestCollectSource:
    def test_calls_collector(self) -> None:
        """Collector is instantiated, collect_discussions and process_discussion called."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_instance = mock_cls.return_value
        mock_instance.collect_discussions.return_value = [
            {"raw_data": {"objectID": "1"}, "source_id": 1, "external_id": "1"},
        ]

        engine = MagicMock()
        result = collect_source(engine, cfg, "hackernews", mock_cls)

        assert result == 1
        mock_instance.collect_discussions.assert_called_once()
        mock_instance.process_discussion.assert_called_once()

    def test_isolates_errors_per_reference(self) -> None:
        """One ref failing process_discussion does not stop other refs."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_instance = mock_cls.return_value
        mock_instance.collect_discussions.return_value = [
            {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "1"},
            {"raw_data": {"id": "2"}, "source_id": 1, "external_id": "2"},
            {"raw_data": {"id": "3"}, "source_id": 1, "external_id": "3"},
        ]
        # Second call to process_discussion raises
        mock_instance.process_discussion.side_effect = [None, RuntimeError("bad ref"), None]

        engine = MagicMock()
        result = collect_source(engine, cfg, "hackernews", mock_cls)

        # First and third succeed, second fails -> 2 processed
        assert result == 2
        assert mock_instance.process_discussion.call_count == 3

    def test_returns_count(self) -> None:
        """Return value matches number of successfully processed refs."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_cls.return_value.collect_discussions.return_value = [
            {"raw_data": {}, "source_id": 1, "external_id": "a1"},
            {"raw_data": {}, "source_id": 1, "external_id": "a2"},
            {"raw_data": {}, "source_id": 1, "external_id": "a3"},
        ]

        result = collect_source(MagicMock(), cfg, "hackernews", mock_cls)

        assert result == 3

    def test_source_error_propagates(self) -> None:
        """collect_discussions raising propagates — Dagster retry handles it."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_cls.return_value.collect_discussions.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            collect_source(MagicMock(), cfg, "hackernews", mock_cls)


# ---------------------------------------------------------------------------
# fetch_comments
# ---------------------------------------------------------------------------


class TestFetchComments:
    @patch("aggre.dagster_defs.comments.job.COLLECTORS")
    def test_iterates_comment_sources(self, mock_collectors: MagicMock) -> None:
        """Calls collect_comments on reddit, hackernews, and lobsters."""
        mock_reddit_cls = MagicMock()
        mock_reddit_cls.return_value.collect_comments.return_value = 3

        mock_hn_cls = MagicMock()
        mock_hn_cls.return_value.collect_comments.return_value = 5

        mock_lobsters_cls = MagicMock()
        mock_lobsters_cls.return_value.collect_comments.return_value = 2

        mock_collectors.get.side_effect = lambda name: {
            "reddit": mock_reddit_cls,
            "hackernews": mock_hn_cls,
            "lobsters": mock_lobsters_cls,
        }.get(name)

        ctx = _mock_context(MagicMock(), config=make_config())
        result = fetch_comments(ctx)

        assert result == 10
        mock_reddit_cls.return_value.collect_comments.assert_called_once()
        mock_hn_cls.return_value.collect_comments.assert_called_once()
        mock_lobsters_cls.return_value.collect_comments.assert_called_once()

    @patch("aggre.dagster_defs.comments.job.COLLECTORS")
    def test_isolates_errors_per_source(self, mock_collectors: MagicMock) -> None:
        """One source throwing does not stop others from running."""
        mock_reddit_cls = MagicMock()
        mock_reddit_cls.return_value.collect_comments.side_effect = RuntimeError("reddit down")

        mock_hn_cls = MagicMock()
        mock_hn_cls.return_value.collect_comments.return_value = 4

        mock_lobsters_cls = MagicMock()
        mock_lobsters_cls.return_value.collect_comments.return_value = 1

        mock_collectors.get.side_effect = lambda name: {
            "reddit": mock_reddit_cls,
            "hackernews": mock_hn_cls,
            "lobsters": mock_lobsters_cls,
        }.get(name)

        ctx = _mock_context(MagicMock(), config=make_config())
        result = fetch_comments(ctx)

        # Reddit failed, HN + Lobsters succeeded
        assert result == 5
        mock_hn_cls.return_value.collect_comments.assert_called_once()
        mock_lobsters_cls.return_value.collect_comments.assert_called_once()

    @patch("aggre.dagster_defs.comments.job.COLLECTORS")
    def test_returns_total_count(self, mock_collectors: MagicMock) -> None:
        """Return value is the sum of all collected comment counts."""
        mock_reddit_cls = MagicMock()
        mock_reddit_cls.return_value.collect_comments.return_value = 7

        mock_hn_cls = MagicMock()
        mock_hn_cls.return_value.collect_comments.return_value = 0

        mock_lobsters_cls = MagicMock()
        mock_lobsters_cls.return_value.collect_comments.return_value = 3

        mock_collectors.get.side_effect = lambda name: {
            "reddit": mock_reddit_cls,
            "hackernews": mock_hn_cls,
            "lobsters": mock_lobsters_cls,
        }.get(name)

        ctx = _mock_context(MagicMock(), config=make_config())
        result = fetch_comments(ctx)

        assert result == 10

    @patch("aggre.dagster_defs.comments.job.COLLECTORS")
    def test_skips_missing_collector(self, mock_collectors: MagicMock) -> None:
        """If a comment source has no entry in COLLECTORS, it is skipped gracefully."""
        mock_hn_cls = MagicMock()
        mock_hn_cls.return_value.collect_comments.return_value = 2

        # Only hackernews exists; reddit and lobsters return None from .get()
        mock_collectors.get.side_effect = lambda name: {
            "hackernews": mock_hn_cls,
        }.get(name)

        ctx = _mock_context(MagicMock(), config=make_config())
        result = fetch_comments(ctx)

        assert result == 2
        mock_hn_cls.return_value.collect_comments.assert_called_once()
