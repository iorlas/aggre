"""Tests for collect_source orchestration and event emission.

All dependencies (config, collectors, logging, engine) are mocked — no database
or external services required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from hatchet_sdk.clients.events import PushEventOptions

from aggre.collectors.youtube.config import TranscribePolicy, YoutubeConfig, YoutubeSource
from aggre.workflows.collection import _check_youtube_transcribe_policy, _find_youtube_source, collect_source
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
    return


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
                "text_provided": False,
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
        """No event emitted when content already has text (fully processed)."""
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
        mock_result = MagicMock()
        mock_result.first.return_value = mock_disc_row
        connect_mock = MagicMock()
        connect_mock.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)

        assert result.events_skipped == 1
        mock_hatchet.event.push.assert_not_called()

    def test_no_event_for_self_post(self) -> None:
        """Self-posts have text pre-populated by collector — already fully processed,
        no event needed (webpage/transcription will skip, comments use their own query)."""
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
        mock_result = MagicMock()
        mock_result.first.return_value = mock_disc_row
        connect_mock = MagicMock()
        connect_mock.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=connect_mock)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        result = collect_source(engine, cfg, "hackernews", mock_cls, hatchet=mock_hatchet)

        assert result.events_skipped == 1
        mock_hatchet.event.push.assert_not_called()


def _make_youtube_config(*sources: YoutubeSource) -> YoutubeConfig:
    return YoutubeConfig(sources=list(sources))


def _make_ref(*, channel_id: str = "CH1", title: str = "Some Video", duration: int | None = None) -> dict:
    raw: dict = {"_channel_id": channel_id, "title": title}
    if duration is not None:
        raw["duration"] = duration
    return {"external_id": "vid1", "raw_data": raw, "source_id": 1}


class TestFindYoutubeSource:
    def test_finds_matching_source(self) -> None:
        src = YoutubeSource(channel_id="CH1", name="Test")
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _find_youtube_source(cfg, "CH1") is src

    def test_returns_none_for_unknown(self) -> None:
        cfg = make_config(youtube=_make_youtube_config())
        assert _find_youtube_source(cfg, "UNKNOWN") is None


class TestCheckYoutubeTranscribePolicy:
    def test_always_allows(self) -> None:
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.always)
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref()) is None

    def test_never_blocks(self) -> None:
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.never)
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref()) == "policy_never"

    def test_keyword_match_allows(self) -> None:
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.keyword, keywords=["AI", "LLM"])
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref(title="New AI breakthrough")) is None

    def test_keyword_no_match_blocks(self) -> None:
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.keyword, keywords=["AI", "LLM"])
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref(title="Cooking tutorial")) == "policy_keyword_no_match"

    def test_keyword_match_case_insensitive(self) -> None:
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.keyword, keywords=["ai"])
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref(title="New AI Model")) is None

    def test_duration_exceeded_blocks(self) -> None:
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.always, max_duration_minutes=30)
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref(duration=2700)) == "policy_duration_exceeded"

    def test_duration_within_limit_allows(self) -> None:
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.always, max_duration_minutes=60)
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref(duration=1800)) is None

    def test_duration_none_allows(self) -> None:
        """No duration metadata — don't block (can't know length yet)."""
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.always, max_duration_minutes=30)
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref(duration=None)) is None

    def test_unknown_channel_defaults_to_always(self) -> None:
        cfg = make_config(youtube=_make_youtube_config())
        assert _check_youtube_transcribe_policy(cfg, _make_ref(channel_id="UNKNOWN")) is None

    def test_keyword_with_duration_both_checked(self) -> None:
        """Keyword matches but duration exceeds — should block."""
        src = YoutubeSource(channel_id="CH1", name="Test", transcribe=TranscribePolicy.keyword, keywords=["AI"], max_duration_minutes=30)
        cfg = make_config(youtube=_make_youtube_config(src))
        assert _check_youtube_transcribe_policy(cfg, _make_ref(title="AI talk", duration=3600)) == "policy_duration_exceeded"
