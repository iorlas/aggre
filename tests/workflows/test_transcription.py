"""Tests for per-item YouTube transcription (transcribe_one).

Uses real PostgreSQL engine for DB queries, mocks whisper.cpp server via transcribe_audio.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
import sqlalchemy as sa

from aggre.db import SilverContent
from aggre.utils.whisper_client import TranscriptionResult
from aggre.workflows.transcription import transcribe_one
from tests.factories import make_config, seed_content, seed_discussion

pytestmark = pytest.mark.integration


def _get_content(engine: sa.engine.Engine, content_id: int) -> sa.engine.Row:
    """Fetch a SilverContent row by id."""
    with engine.connect() as conn:
        return conn.execute(sa.select(SilverContent).where(SilverContent.id == content_id)).fetchone()


def _seed_youtube(
    engine: sa.engine.Engine,
    external_id: str = "abc123",
    title: str = "Test Video",
    meta: str | None = None,
    text: str | None = None,
) -> int:
    """Seed a SilverContent + SilverDiscussion pair for a YouTube video. Returns content_id."""
    content_id = seed_content(
        engine,
        f"https://youtube.com/watch?v={external_id}",
        domain="youtube.com",
        text=text,
    )
    seed_discussion(
        engine,
        source_type="youtube",
        external_id=external_id,
        content_id=content_id,
        title=title,
        meta=meta,
    )
    return content_id


class TestTranscribeOne:
    def test_returns_skipped_for_nonexistent_content(self, engine):
        config = make_config()

        result = transcribe_one(engine, config, 99999)

        assert result == "skipped"

    def test_returns_skipped_for_non_youtube_content(self, engine):
        """Content not linked to a YouTube discussion is skipped."""
        config = make_config()
        content_id = seed_content(engine, "https://example.com/article", domain="example.com")
        seed_discussion(
            engine,
            source_type="hackernews",
            external_id="hn001",
            content_id=content_id,
        )

        result = transcribe_one(engine, config, content_id)

        assert result == "skipped"

    def test_returns_already_done_when_text_set(self, engine):
        config = make_config()
        content_id = _seed_youtube(engine, external_id="done01", text="Already transcribed")

        result = transcribe_one(engine, config, content_id)

        assert result == "already_done"

    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.read_bronze_or_none")
    def test_uses_cached_whisper_json(self, mock_read_or_none, mock_write, engine):
        """When whisper.json exists in bronze, skip download + transcription."""
        content_id = _seed_youtube(engine, external_id="cached01")
        config = make_config()

        cached_data = json.dumps({"transcript": "Cached transcript", "language": "fr"})
        mock_read_or_none.return_value = cached_data

        result = transcribe_one(engine, config, content_id)
        assert result == "cached"

        row = _get_content(engine, content_id)
        assert row.text == "Cached transcript"
        assert row.detected_language == "fr"

    @patch("aggre.workflows.transcription.transcribe_audio")
    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    @patch("aggre.workflows.transcription.yt_dlp.YoutubeDL")
    def test_transcribes_and_stores_text(
        self,
        mock_ydl_cls,
        mock_read_or_none,
        mock_get_store,
        mock_write,
        mock_transcribe,
        engine,
        tmp_path,
    ):
        """Downloads audio, transcribes, stores text + detected_language on SilverContent."""
        content_id = _seed_youtube(engine, external_id="vid001")
        config = make_config()

        mock_transcribe.return_value = TranscriptionResult(text="This is the transcript", language="en", server_name="test-whisper")

        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_file
        mock_get_store.return_value = mock_store

        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = transcribe_one(engine, config, content_id)
        assert result == "transcribed"

        row = _get_content(engine, content_id)
        assert row.text == "This is the transcript"
        assert row.detected_language == "en"
        assert row.transcribed_by == "test-whisper"

    @patch("aggre.workflows.transcription.transcribe_audio")
    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    def test_uses_cached_audio(self, mock_read_or_none, mock_get_store, mock_write, mock_transcribe, engine, tmp_path):
        """When audio file exists in bronze, skip download but still transcribe."""
        content_id = _seed_youtube(engine, external_id="audio01")
        config = make_config()

        mock_transcribe.return_value = TranscriptionResult(text="Transcribed from cache", language="en", server_name="test-whisper")

        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake cached audio")
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_file
        mock_get_store.return_value = mock_store

        result = transcribe_one(engine, config, content_id)
        assert result == "transcribed"

        row = _get_content(engine, content_id)
        assert row.text == "Transcribed from cache"
        mock_transcribe.assert_called_once()

    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    @patch("aggre.workflows.transcription.yt_dlp.YoutubeDL")
    def test_download_error_propagates(self, mock_ydl_cls, mock_read_or_none, mock_get_store, mock_write, engine, tmp_path):
        """yt-dlp failure propagates for Hatchet retry."""
        _seed_youtube(engine, external_id="fail01")
        config = make_config()

        audio_file = tmp_path / "nonexistent_audio.opus"
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_file
        mock_get_store.return_value = mock_store

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = Exception("Video unavailable")
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(Exception, match="Video unavailable"):
            transcribe_one(engine, config, _seed_youtube(engine, external_id="fail02"))

    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.read_bronze_or_none")
    def test_processes_short_video(self, mock_read_or_none, mock_write, engine):
        """Videos under 30 minutes are NOT skipped."""
        meta = json.dumps({"duration": 1200, "channel_id": "UC123"})
        content_id = _seed_youtube(engine, external_id="short01", title="Short Video", meta=meta)
        config = make_config()

        cached_data = json.dumps({"transcript": "Short transcript", "language": "en"})
        mock_read_or_none.return_value = cached_data

        result = transcribe_one(engine, config, content_id)
        assert result == "cached"

    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.read_bronze_or_none")
    def test_processes_video_without_duration(self, mock_read_or_none, mock_write, engine):
        """Videos with no duration in meta are NOT skipped."""
        meta = json.dumps({"channel_id": "UC123"})
        content_id = _seed_youtube(engine, external_id="nodur01", title="No Duration", meta=meta)
        config = make_config()

        cached_data = json.dumps({"transcript": "Transcript", "language": "en"})
        mock_read_or_none.return_value = cached_data

        result = transcribe_one(engine, config, content_id)
        assert result == "cached"

    @patch("aggre.workflows.transcription.transcribe_audio")
    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    @patch("aggre.workflows.transcription.yt_dlp.YoutubeDL")
    def test_writes_whisper_output_to_bronze(
        self,
        mock_ydl_cls,
        mock_read_or_none,
        mock_get_store,
        mock_write,
        mock_transcribe,
        engine,
        tmp_path,
    ):
        """Verify whisper.json is written to bronze after transcription."""
        content_id = _seed_youtube(engine, external_id="bronze01")
        config = make_config()

        mock_transcribe.return_value = TranscriptionResult(text="Hello world", language="de", server_name="test-whisper")

        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio")
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_file
        mock_get_store.return_value = mock_store

        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        transcribe_one(engine, config, content_id)

        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == "youtube"
        assert call_args[0][1] == "bronze01"
        assert call_args[0][2] == "whisper"

        written_json = json.loads(call_args[0][3])
        assert written_json["transcript"] == "Hello world"
        assert written_json["language"] == "de"
        assert "language_probability" not in written_json

    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    @patch("aggre.workflows.transcription.yt_dlp.YoutubeDL")
    def test_skips_large_audio_file(self, mock_ydl_cls, mock_read_or_none, mock_get_store, mock_write, engine, tmp_path):
        """Audio >500MB raises ValueError (Hatchet retries or gives up)."""
        content_id = _seed_youtube(engine, external_id="big01")
        config = make_config()

        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"x")
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_file
        mock_get_store.return_value = mock_store

        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(type(audio_file), "stat", return_value=MagicMock(st_size=600 * 1024 * 1024)):
            with pytest.raises(ValueError, match="500MB"):
                transcribe_one(engine, config, content_id)

    @patch("aggre.workflows.transcription.read_bronze_or_none")
    def test_cache_check_exception_propagates(self, mock_read_or_none, engine):
        """If read_bronze_or_none raises (e.g. S3 unreachable), error propagates for Hatchet retry."""
        content_id = _seed_youtube(engine, external_id="crash01")
        config = make_config()

        mock_read_or_none.side_effect = ConnectionError("S3 unreachable")

        with pytest.raises(ConnectionError, match="S3 unreachable"):
            transcribe_one(engine, config, content_id)

    @patch("aggre.workflows.transcription.transcribe_audio")
    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    @patch("aggre.workflows.transcription.yt_dlp.YoutubeDL")
    def test_transcription_server_error_propagates(
        self,
        mock_ydl_cls,
        mock_read_or_none,
        mock_get_store,
        mock_write,
        mock_transcribe,
        engine,
        tmp_path,
    ):
        """whisper.cpp server failure propagates for Hatchet retry."""
        content_id = _seed_youtube(engine, external_id="terr01")
        config = make_config()

        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio")
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_file
        mock_get_store.return_value = mock_store

        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_transcribe.side_effect = httpx.ConnectError("server down")

        with pytest.raises(httpx.ConnectError, match="server down"):
            transcribe_one(engine, config, content_id)

    @patch("aggre.workflows.transcription.transcribe_audio")
    @patch("aggre.workflows.transcription.write_bronze")
    @patch("aggre.workflows.transcription.get_store")
    @patch("aggre.workflows.transcription.read_bronze_or_none", return_value=None)
    @patch("aggre.workflows.transcription.yt_dlp.YoutubeDL")
    def test_ydl_opts_include_impersonate(
        self,
        mock_ydl_cls,
        mock_read_or_none,
        mock_get_store,
        mock_write,
        mock_transcribe,
        engine,
        tmp_path,
    ):
        """yt-dlp opts always include impersonate=chrome for TLS fingerprint evasion."""
        content_id = _seed_youtube(engine, external_id="imp01")
        config = make_config()

        mock_transcribe.return_value = TranscriptionResult(text="Hello", language="en", server_name="test-whisper")

        # audio_dest must NOT exist so the download path is triggered
        audio_dest = tmp_path / "audio.opus"
        mock_store = MagicMock()
        mock_store.local_path.return_value = audio_dest
        mock_get_store.return_value = mock_store

        # Side-effect: create the expected file so the glob finds it
        def fake_download(urls):
            (audio_dest.parent / "imp01.opus").write_bytes(b"fake audio data")

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = fake_download
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        transcribe_one(engine, config, content_id)

        opts = mock_ydl_cls.call_args[0][0]
        assert opts["impersonate"] == "chrome"

    def test_empty_whisper_endpoints_raises(self, engine):
        """When whisper_endpoints is empty, transcription raises RuntimeError (Hatchet retries)."""
        content_id = _seed_youtube(engine, external_id="nourl01")
        config = make_config(whisper_endpoints="")

        with pytest.raises(RuntimeError, match="AGGRE_WHISPER_ENDPOINTS not configured"):
            transcribe_one(engine, config, content_id)
