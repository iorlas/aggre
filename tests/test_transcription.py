"""Tests for YouTube transcription pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.dagster_defs.transcription.job import transcribe
from aggre.db import SilverContent
from tests.factories import make_config, seed_content, seed_observation

pytestmark = pytest.mark.integration


def _get_content(engine: sa.engine.Engine, content_id: int) -> sa.engine.Row:
    """Fetch a SilverContent row by id."""
    with engine.connect() as conn:
        return conn.execute(sa.select(SilverContent).where(SilverContent.id == content_id)).fetchone()


def _seed_youtube(engine: sa.engine.Engine, external_id: str = "abc123", title: str = "Test Video") -> int:
    """Seed a SilverContent + SilverObservation pair for a YouTube video. Returns content_id."""
    content_id = seed_content(
        engine,
        f"https://youtube.com/watch?v={external_id}",
        domain="youtube.com",
    )
    seed_observation(
        engine,
        source_type="youtube",
        external_id=external_id,
        content_id=content_id,
        title=title,
    )
    return content_id


def _make_mock_model(transcript_text: str = "This is the transcript", language: str = "en") -> MagicMock:
    """Build a mock WhisperModel that returns a single segment."""
    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = transcript_text
    mock_info = MagicMock()
    mock_info.language = language
    mock_info.language_probability = 0.95
    mock_model.transcribe.return_value = ([mock_segment], mock_info)
    return mock_model


class TestTranscribe:
    def test_no_pending_returns_zero(self, engine):
        """No YouTube content needing transcription."""
        config = make_config()
        result = transcribe(engine, config)
        assert result == 0

    @patch("aggre.dagster_defs.transcription.job.write_bronze")
    @patch("aggre.dagster_defs.transcription.job.bronze_path")
    @patch("aggre.dagster_defs.transcription.job.bronze_exists", return_value=False)
    @patch("aggre.dagster_defs.transcription.job.yt_dlp.YoutubeDL")
    def test_transcribes_and_stores_text(self, mock_ydl_cls, mock_exists, mock_path, mock_write, engine, tmp_path):
        """Downloads audio, transcribes, stores text + detected_language on SilverContent."""
        content_id = _seed_youtube(engine, external_id="vid001")
        config = make_config()
        mock_model = _make_mock_model()

        # Set up audio file so the code finds it after "download"
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")
        mock_path.return_value = audio_file

        # Mock YoutubeDL context manager
        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = transcribe(engine, config, model=mock_model)
        assert result == 1

        row = _get_content(engine, content_id)
        assert row.text == "This is the transcript"
        assert row.detected_language == "en"
        assert row.error is None

    @patch("aggre.dagster_defs.transcription.job.write_bronze")
    @patch("aggre.dagster_defs.transcription.job.read_bronze")
    @patch("aggre.dagster_defs.transcription.job.bronze_exists")
    def test_uses_cached_whisper_json(self, mock_exists, mock_read, mock_write, engine, tmp_path):
        """When whisper.json exists in bronze, skip download + transcription."""
        content_id = _seed_youtube(engine, external_id="cached01")
        config = make_config()

        cached_data = json.dumps({"transcript": "Cached transcript", "language": "fr"})
        mock_exists.return_value = True
        mock_read.return_value = cached_data

        result = transcribe(engine, config)
        assert result == 1

        row = _get_content(engine, content_id)
        assert row.text == "Cached transcript"
        assert row.detected_language == "fr"
        assert row.error is None

    @patch("aggre.dagster_defs.transcription.job.write_bronze")
    @patch("aggre.dagster_defs.transcription.job.bronze_path")
    @patch("aggre.dagster_defs.transcription.job.bronze_exists", return_value=False)
    def test_uses_cached_audio(self, mock_exists, mock_path, mock_write, engine, tmp_path):
        """When audio file exists in bronze, skip download but still transcribe."""
        content_id = _seed_youtube(engine, external_id="audio01")
        config = make_config()
        mock_model = _make_mock_model(transcript_text="Transcribed from cache")

        # Audio file already exists on disk
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake cached audio")
        mock_path.return_value = audio_file

        result = transcribe(engine, config, model=mock_model)
        assert result == 1

        row = _get_content(engine, content_id)
        assert row.text == "Transcribed from cache"
        assert row.error is None
        # WhisperModel.transcribe should have been called
        mock_model.transcribe.assert_called_once()

    @patch("aggre.dagster_defs.transcription.job.write_bronze")
    @patch("aggre.dagster_defs.transcription.job.bronze_path")
    @patch("aggre.dagster_defs.transcription.job.bronze_exists", return_value=False)
    @patch("aggre.dagster_defs.transcription.job.yt_dlp.YoutubeDL")
    def test_handles_download_error(self, mock_ydl_cls, mock_exists, mock_path, mock_write, engine, tmp_path):
        """yt-dlp fails -> error column set on SilverContent."""
        content_id = _seed_youtube(engine, external_id="fail01")
        config = make_config()

        # Audio file does not exist on disk
        audio_file = tmp_path / "nonexistent_audio.opus"
        mock_path.return_value = audio_file

        # Make YoutubeDL.download raise
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = Exception("Video unavailable")
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = transcribe(engine, config)
        assert result == 0

        row = _get_content(engine, content_id)
        assert row.text is None
        assert row.error is not None
        assert "Video unavailable" in row.error

    @patch("aggre.dagster_defs.transcription.job.write_bronze")
    @patch("aggre.dagster_defs.transcription.job.bronze_path")
    @patch("aggre.dagster_defs.transcription.job.bronze_exists", return_value=False)
    @patch("aggre.dagster_defs.transcription.job.yt_dlp.YoutubeDL")
    def test_handles_transcription_error(self, mock_ydl_cls, mock_exists, mock_path, mock_write, engine, tmp_path):
        """WhisperModel fails -> error column set on SilverContent."""
        content_id = _seed_youtube(engine, external_id="terr01")
        config = make_config()

        # Set up audio file
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio")
        mock_path.return_value = audio_file

        # Mock YoutubeDL context manager
        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        # WhisperModel raises
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("CUDA out of memory")

        result = transcribe(engine, config, model=mock_model)
        assert result == 0

        row = _get_content(engine, content_id)
        assert row.text is None
        assert row.error is not None
        assert "CUDA out of memory" in row.error

    @patch("aggre.dagster_defs.transcription.job.write_bronze")
    @patch("aggre.dagster_defs.transcription.job.bronze_path")
    @patch("aggre.dagster_defs.transcription.job.bronze_exists", return_value=False)
    @patch("aggre.dagster_defs.transcription.job.yt_dlp.YoutubeDL")
    def test_skips_large_audio_file(self, mock_ydl_cls, mock_exists, mock_path, mock_write, engine, tmp_path):
        """Audio >500MB -> error 'exceeds 500MB' set."""
        content_id = _seed_youtube(engine, external_id="big01")
        config = make_config()

        # Create a file and fake its size via stat
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"x")
        mock_path.return_value = audio_file

        # Mock YoutubeDL context manager
        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Patch Path.stat to report > 500MB
        with patch.object(type(audio_file), "stat", return_value=MagicMock(st_size=600 * 1024 * 1024)):
            result = transcribe(engine, config)

        assert result == 0

        row = _get_content(engine, content_id)
        assert row.text is None
        assert row.error is not None
        assert "500MB" in row.error

    @patch("aggre.dagster_defs.transcription.job.write_bronze")
    @patch("aggre.dagster_defs.transcription.job.bronze_path")
    @patch("aggre.dagster_defs.transcription.job.bronze_exists", return_value=False)
    @patch("aggre.dagster_defs.transcription.job.yt_dlp.YoutubeDL")
    def test_respects_batch_limit(self, mock_ydl_cls, mock_exists, mock_path, mock_write, engine, tmp_path):
        """batch_limit=1 with 2 pending -> only 1 processed."""
        _seed_youtube(engine, external_id="batch01", title="Video 1")
        _seed_youtube(engine, external_id="batch02", title="Video 2")
        config = make_config()
        mock_model = _make_mock_model()

        # Set up audio file
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio")
        mock_path.return_value = audio_file

        # Mock YoutubeDL context manager
        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = transcribe(engine, config, batch_limit=1, model=mock_model)
        assert result == 1

        # Verify only one row was transcribed
        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).where(SilverContent.text.isnot(None))).fetchall()
            assert len(rows) == 1

            pending = conn.execute(
                sa.select(SilverContent).where(
                    SilverContent.text.is_(None),
                    SilverContent.error.is_(None),
                )
            ).fetchall()
            assert len(pending) == 1

    @patch("aggre.dagster_defs.transcription.job.write_bronze")
    @patch("aggre.dagster_defs.transcription.job.bronze_path")
    @patch("aggre.dagster_defs.transcription.job.bronze_exists", return_value=False)
    @patch("aggre.dagster_defs.transcription.job.yt_dlp.YoutubeDL")
    def test_writes_whisper_output_to_bronze(self, mock_ydl_cls, mock_exists, mock_path, mock_write, engine, tmp_path):
        """Verify whisper.json is written to bronze after transcription."""
        _seed_youtube(engine, external_id="bronze01")
        config = make_config()
        mock_model = _make_mock_model(transcript_text="Hello world", language="de")

        # Set up audio file
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio")
        mock_path.return_value = audio_file

        # Mock YoutubeDL context manager
        mock_ydl_instance = MagicMock()
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        transcribe(engine, config, model=mock_model)

        # Verify write_bronze was called with correct args
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == "youtube"
        assert call_args[0][1] == "bronze01"
        assert call_args[0][2] == "whisper"

        # Verify the JSON content
        written_json = json.loads(call_args[0][3])
        assert written_json["transcript"] == "Hello world"
        assert written_json["language"] == "de"
        assert written_json["language_probability"] == 0.95

        assert call_args[0][4] == "json"
