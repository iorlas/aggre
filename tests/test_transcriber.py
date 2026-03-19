"""Tests for the transcriber abstraction layer."""

from __future__ import annotations

import pytest

from aggre.transcriber import (
    AllTranscribersFailedError,
    QuotaExceededError,
    TranscriptResult,
)

pytestmark = pytest.mark.unit


class TestTranscriptResult:
    def test_frozen(self):
        r = TranscriptResult(text="hello", language="en", transcribed_by="test")
        with pytest.raises(AttributeError):
            r.text = "changed"

    def test_fields(self):
        r = TranscriptResult(text="hello", language="en", transcribed_by="modal-a10g")
        assert r.text == "hello"
        assert r.language == "en"
        assert r.transcribed_by == "modal-a10g"


class TestExceptions:
    def test_quota_exceeded_is_exception(self):
        assert issubclass(QuotaExceededError, Exception)

    def test_all_transcribers_failed_is_exception(self):
        assert issubclass(AllTranscribersFailedError, Exception)


from aggre.transcriber import transcribe_with_fallback


class TestTranscribeWithFallback:
    def test_first_backend_succeeds(self):
        def backend(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            return TranscriptResult(text="ok", language="en", transcribed_by="first")

        result = transcribe_with_fallback([backend], b"audio")
        assert result.text == "ok"
        assert result.transcribed_by == "first"

    def test_falls_back_on_quota_exceeded(self):
        def failing(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            raise QuotaExceededError("out of credits")

        def fallback(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            return TranscriptResult(text="fallback", language="en", transcribed_by="second")

        result = transcribe_with_fallback([failing, fallback], b"audio")
        assert result.transcribed_by == "second"

    def test_falls_back_on_connection_error(self):
        def failing(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            raise ConnectionError("network down")

        def fallback(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            return TranscriptResult(text="ok", language="en", transcribed_by="backup")

        result = transcribe_with_fallback([failing, fallback], b"audio")
        assert result.transcribed_by == "backup"

    def test_does_not_fall_back_on_transcription_error(self):
        """Non-fallback errors (bad audio, model failure) propagate immediately."""
        def failing(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            raise ValueError("bad audio format")

        def fallback(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            return TranscriptResult(text="ok", language="en", transcribed_by="backup")

        with pytest.raises(ValueError, match="bad audio"):
            transcribe_with_fallback([failing, fallback], b"audio")

    def test_all_fail_raises_all_transcribers_failed(self):
        def failing(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            raise QuotaExceededError("out")

        with pytest.raises(AllTranscribersFailedError):
            transcribe_with_fallback([failing], b"audio")

    def test_empty_list_raises_all_transcribers_failed(self):
        with pytest.raises(AllTranscribersFailedError):
            transcribe_with_fallback([], b"audio")

    def test_passes_format_hint(self):
        received = {}

        def backend(audio: bytes, format_hint: str = "opus") -> TranscriptResult:
            received["format_hint"] = format_hint
            return TranscriptResult(text="ok", language="en", transcribed_by="test")

        transcribe_with_fallback([backend], b"audio", format_hint="wav")
        assert received["format_hint"] == "wav"


from unittest.mock import MagicMock, patch

from aggre.transcriber import WhisperTranscriber
from aggre.utils.whisper_client import Endpoint, TranscriptionResult


class TestWhisperTranscriber:
    def _make_endpoint(self) -> Endpoint:
        return Endpoint(url="http://test:8090", weight=1, api_format="whisper-cpp", name="test-whisper", max_concurrent=1)

    @patch("aggre.transcriber.transcribe_audio")
    def test_transcribes_and_maps_result(self, mock_transcribe, tmp_path):
        mock_transcribe.return_value = TranscriptionResult(
            text="Hello world", language="en", server_name="test-whisper"
        )
        endpoints = [self._make_endpoint()]
        whisper = WhisperTranscriber(endpoints=endpoints, model="large-v3-turbo", timeout=300.0)

        result = whisper(b"fake audio", "opus")

        assert result.text == "Hello world"
        assert result.language == "en"
        assert result.transcribed_by == "test-whisper"

        call_args = mock_transcribe.call_args
        audio_path = call_args[0][0]
        assert audio_path.suffix == ".opus"
        assert call_args[1]["endpoints"] == endpoints
        assert call_args[1]["model"] == "large-v3-turbo"

    @patch("aggre.transcriber.transcribe_audio")
    def test_format_hint_used_as_extension(self, mock_transcribe):
        mock_transcribe.return_value = TranscriptionResult(
            text="ok", language="en", server_name="test"
        )
        whisper = WhisperTranscriber(
            endpoints=[self._make_endpoint()], model="large-v3-turbo", timeout=300.0
        )

        whisper(b"fake", "wav")

        audio_path = mock_transcribe.call_args[0][0]
        assert audio_path.suffix == ".wav"

    @patch("aggre.transcriber.transcribe_audio")
    def test_connection_error_propagates(self, mock_transcribe):
        mock_transcribe.side_effect = ConnectionError("All endpoints failed")
        whisper = WhisperTranscriber(
            endpoints=[self._make_endpoint()], model="large-v3-turbo", timeout=300.0
        )

        with pytest.raises(ConnectionError):
            whisper(b"fake", "opus")
