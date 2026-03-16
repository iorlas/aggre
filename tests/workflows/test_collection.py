"""Tests for collect_source orchestration and event emission.

All dependencies (config, collectors, logging, engine) are mocked — no database
or external services required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from hatchet_sdk.clients.events import PushEventOptions

from aggre.workflows.collection import collect_source
from aggre.workflows.models import CollectResult
from tests.factories import make_config

pytestmark = pytest.mark.integration


# Override session-scoped fixtures from conftest.py so these tests run without PostgreSQL.
@pytest.fixture()
def engine():
    """No-op engine override — orchestration tests use mocks, not a real DB."""
    return MagicMock()


@pytest.fixture(autouse=True)
def clean_tables():
    """No-op override of the autouse table-truncation fixture."""
    yield


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

        assert result == CollectResult(source="hackernews", succeeded=1, failed=0, total=1)
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
        assert result == CollectResult(source="hackernews", succeeded=2, failed=1, total=3)
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

        assert result == CollectResult(source="hackernews", succeeded=3, failed=0, total=3)

    def test_source_error_propagates(self) -> None:
        """collect_discussions raising propagates — retry handles it."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_cls.return_value.collect_discussions.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            collect_source(MagicMock(), cfg, "hackernews", mock_cls)


class TestEventEmission:
    def test_emits_item_new_event(self) -> None:
        """After successful process_discussion, emits item.new event."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_instance = mock_cls.return_value
        mock_instance.collect_discussions.return_value = [
            {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "ext1"},
        ]

        mock_hatchet = MagicMock()

        # Mock engine with DB query results for event emission
        engine = MagicMock()
        mock_conn = MagicMock()
        engine.begin.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.begin.return_value.__exit__ = MagicMock(return_value=False)

        # Mock the event lookup query
        mock_disc_row = MagicMock()
        mock_disc_row.id = 42
        mock_disc_row.content_id = 100
        mock_disc_row.domain = "example.com"
        mock_disc_row.text = None
        mock_disc_row.discussions_searched_at = None
        mock_result = MagicMock()
        mock_result.first.return_value = mock_disc_row
        connect_mock = MagicMock()
        connect_mock.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)

        mock_hatchet.event.push.assert_called_once_with(
            "item.new",
            {
                "content_id": 100,
                "discussion_id": 42,
                "source": "hackernews",
                "domain": "example.com",
            },
            options=PushEventOptions(scope="default"),
        )

    def test_no_event_without_hatchet(self) -> None:
        """When hatchet is None, no events are emitted."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_cls.return_value.collect_discussions.return_value = [
            {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "ext1"},
        ]

        engine = MagicMock()
        # Should not raise or try to emit events
        collect_source(engine, cfg, "hackernews", mock_cls, hatchet=None)

    def test_event_emission_error_doesnt_crash(self) -> None:
        """If event emission fails, collection continues."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_instance = mock_cls.return_value
        mock_instance.collect_discussions.return_value = [
            {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "ext1"},
            {"raw_data": {"id": "2"}, "source_id": 1, "external_id": "ext2"},
        ]

        mock_hatchet = MagicMock()
        mock_hatchet.event.push.side_effect = Exception("Hatchet down")

        engine = MagicMock()
        mock_disc_row = MagicMock()
        mock_disc_row.id = 42
        mock_disc_row.content_id = 100
        mock_disc_row.domain = "example.com"
        mock_disc_row.text = None
        mock_disc_row.discussions_searched_at = None
        mock_result = MagicMock()
        mock_result.first.return_value = mock_disc_row
        connect_mock = MagicMock()
        connect_mock.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Should not raise — event emission failure is logged, not propagated
        result = collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)
        assert result == CollectResult(source="hackernews", succeeded=2, failed=0, total=2, event_errors=2, events_skipped=0)

    def test_no_event_when_content_id_null(self) -> None:
        """No event emitted when discussion has no content_id."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_cls.return_value.collect_discussions.return_value = [
            {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "ext1"},
        ]

        mock_hatchet = MagicMock()

        engine = MagicMock()
        mock_disc_row = MagicMock()
        mock_disc_row.id = 42
        mock_disc_row.content_id = None  # No content linked
        mock_disc_row.domain = None
        mock_result = MagicMock()
        mock_result.first.return_value = mock_disc_row
        connect_mock = MagicMock()
        connect_mock.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)

        mock_hatchet.event.push.assert_not_called()

    def test_no_event_when_fully_processed(self) -> None:
        """No event emitted when content has text AND discussions_searched_at (fully processed)."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_cls.return_value.collect_discussions.return_value = [
            {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "ext1"},
        ]

        mock_hatchet = MagicMock()

        engine = MagicMock()
        mock_disc_row = MagicMock()
        mock_disc_row.id = 42
        mock_disc_row.content_id = 100
        mock_disc_row.domain = "example.com"
        mock_disc_row.text = "Some article text"
        mock_disc_row.discussions_searched_at = "2026-03-16T00:00:00+00:00"
        mock_result = MagicMock()
        mock_result.first.return_value = mock_disc_row
        connect_mock = MagicMock()
        connect_mock.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)

        assert result.events_skipped == 1
        mock_hatchet.event.push.assert_not_called()

    def test_emits_event_for_self_post(self) -> None:
        """Self-posts have text pre-populated by collector but discussions_searched_at=None.
        Event must still be emitted so discussion-search and comments run."""
        cfg = make_config()
        mock_cls = MagicMock()
        mock_cls.return_value.collect_discussions.return_value = [
            {"raw_data": {"id": "1"}, "source_id": 1, "external_id": "ext1"},
        ]

        mock_hatchet = MagicMock()

        engine = MagicMock()
        mock_disc_row = MagicMock()
        mock_disc_row.id = 42
        mock_disc_row.content_id = 100
        mock_disc_row.domain = "reddit.com"
        mock_disc_row.text = "This is a Reddit self-post"
        mock_disc_row.discussions_searched_at = None  # Not yet searched
        mock_result = MagicMock()
        mock_result.first.return_value = mock_disc_row
        connect_mock = MagicMock()
        connect_mock.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)

        mock_hatchet.event.push.assert_called_once()
