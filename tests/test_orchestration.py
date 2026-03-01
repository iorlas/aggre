"""Tests for collection and comments orchestration ops.

All dependencies (config, collectors, logging, engine) are mocked — no database
or external services required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aggre.dagster_defs.collection.job import collect_all_sources as _collect_op
from aggre.dagster_defs.comments.job import fetch_comments as _comments_op
from tests.factories import make_config

pytestmark = pytest.mark.integration

# Extract the raw Python functions from the Dagster @op wrappers so we can call
# them directly with a MagicMock context, bypassing Dagster's invocation validation.
collect_all_sources = _collect_op.compute_fn.decorated_fn  # type: ignore[union-attr]
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


def _mock_context(engine: object) -> MagicMock:
    """Create a mock Dagster OpExecutionContext with a database resource."""
    ctx = MagicMock()
    ctx.resources.database.get_engine.return_value = engine
    return ctx


# ---------------------------------------------------------------------------
# collect_all_sources
# ---------------------------------------------------------------------------


class TestCollectAllSources:
    @patch("aggre.dagster_defs.collection.job.COLLECTORS")
    @patch("aggre.dagster_defs.collection.job.setup_logging")
    @patch("aggre.dagster_defs.collection.job.load_config")
    def test_calls_all_configured_collectors(
        self, mock_load_config: MagicMock, mock_logging: MagicMock, mock_collectors: MagicMock
    ) -> None:
        """All collectors in COLLECTORS dict are instantiated and called."""
        mock_load_config.return_value = make_config()
        mock_logging.return_value = MagicMock()

        mock_hn_cls = MagicMock()
        mock_hn_instance = mock_hn_cls.return_value
        mock_hn_instance.collect_references.return_value = [
            {"raw_data": {"objectID": "1"}, "source_id": 1, "external_id": "1"},
        ]

        mock_reddit_cls = MagicMock()
        mock_reddit_instance = mock_reddit_cls.return_value
        mock_reddit_instance.collect_references.return_value = [
            {"raw_data": {"name": "t3_abc"}, "source_id": 2, "external_id": "abc"},
        ]

        mock_collectors.items.return_value = {
            "hackernews": mock_hn_cls,
            "reddit": mock_reddit_cls,
        }.items()

        ctx = _mock_context(MagicMock())
        result = collect_all_sources(ctx)

        assert result == 2
        mock_hn_instance.collect_references.assert_called_once()
        mock_reddit_instance.collect_references.assert_called_once()
        mock_hn_instance.process_reference.assert_called_once()
        mock_reddit_instance.process_reference.assert_called_once()

    @patch("aggre.dagster_defs.collection.job.COLLECTORS")
    @patch("aggre.dagster_defs.collection.job.setup_logging")
    @patch("aggre.dagster_defs.collection.job.load_config")
    def test_isolates_errors_per_source(self, mock_load_config: MagicMock, mock_logging: MagicMock, mock_collectors: MagicMock) -> None:
        """One collector throwing does not stop others from running."""
        mock_load_config.return_value = make_config()
        mock_logging.return_value = MagicMock()

        # First collector raises during collect_references
        mock_failing_cls = MagicMock()
        mock_failing_cls.return_value.collect_references.side_effect = RuntimeError("boom")

        # Second collector succeeds
        mock_ok_cls = MagicMock()
        mock_ok_instance = mock_ok_cls.return_value
        mock_ok_instance.collect_references.return_value = [
            {"raw_data": {"id": "x"}, "source_id": 1, "external_id": "x"},
        ]

        mock_collectors.items.return_value = {
            "hackernews": mock_failing_cls,
            "reddit": mock_ok_cls,
        }.items()

        ctx = _mock_context(MagicMock())
        result = collect_all_sources(ctx)

        assert result == 1
        mock_ok_instance.collect_references.assert_called_once()
        mock_ok_instance.process_reference.assert_called_once()

    @patch("aggre.dagster_defs.collection.job.COLLECTORS")
    @patch("aggre.dagster_defs.collection.job.setup_logging")
    @patch("aggre.dagster_defs.collection.job.load_config")
    def test_isolates_errors_per_reference(self, mock_load_config: MagicMock, mock_logging: MagicMock, mock_collectors: MagicMock) -> None:
        """One ref failing process_reference does not stop other refs."""
        mock_load_config.return_value = make_config()
        mock_logging.return_value = MagicMock()

        mock_cls = MagicMock()
        mock_instance = mock_cls.return_value
        mock_instance.collect_references.return_value = [
            {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "1"},
            {"raw_data": {"id": "2"}, "source_id": 1, "external_id": "2"},
            {"raw_data": {"id": "3"}, "source_id": 1, "external_id": "3"},
        ]
        # Second call to process_reference raises
        mock_instance.process_reference.side_effect = [None, RuntimeError("bad ref"), None]

        mock_collectors.items.return_value = {"hackernews": mock_cls}.items()

        # engine.begin() must return a context-manager mock
        mock_engine = MagicMock()
        ctx = _mock_context(mock_engine)
        result = collect_all_sources(ctx)

        # First and third succeed, second fails -> 2 processed
        assert result == 2
        assert mock_instance.process_reference.call_count == 3

    @patch("aggre.dagster_defs.collection.job.COLLECTORS")
    @patch("aggre.dagster_defs.collection.job.setup_logging")
    @patch("aggre.dagster_defs.collection.job.load_config")
    def test_returns_total_count(self, mock_load_config: MagicMock, mock_logging: MagicMock, mock_collectors: MagicMock) -> None:
        """Return value is the sum of all successfully processed references."""
        mock_load_config.return_value = make_config()
        mock_logging.return_value = MagicMock()

        mock_a_cls = MagicMock()
        mock_a_cls.return_value.collect_references.return_value = [
            {"raw_data": {}, "source_id": 1, "external_id": "a1"},
            {"raw_data": {}, "source_id": 1, "external_id": "a2"},
        ]

        mock_b_cls = MagicMock()
        mock_b_cls.return_value.collect_references.return_value = [
            {"raw_data": {}, "source_id": 2, "external_id": "b1"},
        ]

        mock_c_cls = MagicMock()
        mock_c_cls.return_value.collect_references.return_value = []

        mock_collectors.items.return_value = {
            "hackernews": mock_a_cls,
            "reddit": mock_b_cls,
            "lobsters": mock_c_cls,
        }.items()

        ctx = _mock_context(MagicMock())
        result = collect_all_sources(ctx)

        assert result == 3


# ---------------------------------------------------------------------------
# fetch_comments
# ---------------------------------------------------------------------------


class TestFetchComments:
    @patch("aggre.dagster_defs.comments.job.COLLECTORS")
    @patch("aggre.dagster_defs.comments.job.setup_logging")
    @patch("aggre.dagster_defs.comments.job.load_config")
    def test_iterates_comment_sources(self, mock_load_config: MagicMock, mock_logging: MagicMock, mock_collectors: MagicMock) -> None:
        """Calls collect_comments on reddit, hackernews, and lobsters."""
        mock_load_config.return_value = make_config()
        mock_logging.return_value = MagicMock()

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

        ctx = _mock_context(MagicMock())
        result = fetch_comments(ctx)

        assert result == 10
        mock_reddit_cls.return_value.collect_comments.assert_called_once()
        mock_hn_cls.return_value.collect_comments.assert_called_once()
        mock_lobsters_cls.return_value.collect_comments.assert_called_once()

    @patch("aggre.dagster_defs.comments.job.COLLECTORS")
    @patch("aggre.dagster_defs.comments.job.setup_logging")
    @patch("aggre.dagster_defs.comments.job.load_config")
    def test_isolates_errors_per_source(self, mock_load_config: MagicMock, mock_logging: MagicMock, mock_collectors: MagicMock) -> None:
        """One source throwing does not stop others from running."""
        mock_load_config.return_value = make_config()
        mock_logging.return_value = MagicMock()

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

        ctx = _mock_context(MagicMock())
        result = fetch_comments(ctx)

        # Reddit failed, HN + Lobsters succeeded
        assert result == 5
        mock_hn_cls.return_value.collect_comments.assert_called_once()
        mock_lobsters_cls.return_value.collect_comments.assert_called_once()

    @patch("aggre.dagster_defs.comments.job.COLLECTORS")
    @patch("aggre.dagster_defs.comments.job.setup_logging")
    @patch("aggre.dagster_defs.comments.job.load_config")
    def test_returns_total_count(self, mock_load_config: MagicMock, mock_logging: MagicMock, mock_collectors: MagicMock) -> None:
        """Return value is the sum of all collected comment counts."""
        mock_load_config.return_value = make_config()
        mock_logging.return_value = MagicMock()

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

        ctx = _mock_context(MagicMock())
        result = fetch_comments(ctx)

        assert result == 10

    @patch("aggre.dagster_defs.comments.job.COLLECTORS")
    @patch("aggre.dagster_defs.comments.job.setup_logging")
    @patch("aggre.dagster_defs.comments.job.load_config")
    def test_skips_missing_collector(self, mock_load_config: MagicMock, mock_logging: MagicMock, mock_collectors: MagicMock) -> None:
        """If a comment source has no entry in COLLECTORS, it is skipped gracefully."""
        mock_load_config.return_value = make_config()
        mock_logging.return_value = MagicMock()

        mock_hn_cls = MagicMock()
        mock_hn_cls.return_value.collect_comments.return_value = 2

        # Only hackernews exists; reddit and lobsters return None from .get()
        mock_collectors.get.side_effect = lambda name: {
            "hackernews": mock_hn_cls,
        }.get(name)

        ctx = _mock_context(MagicMock())
        result = fetch_comments(ctx)

        assert result == 2
        mock_hn_cls.return_value.collect_comments.assert_called_once()
