"""Tests for whisper.cpp HTTP client."""

from __future__ import annotations

import httpx
import pytest
import respx

from aggre.utils.whisper_client import TranscriptionResult, transcribe_audio

pytestmark = pytest.mark.unit


class TestTranscribeAudio:
    @respx.mock
    def test_successful_transcription(self, tmp_path):
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://whisper:8090/v1/audio/transcriptions").mock(
            return_value=httpx.Response(
                200,
                json={"text": " Hello world ", "language": "en"},
            )
        )

        result = transcribe_audio(audio_file, server_url="http://whisper:8090", model="large-v3-turbo")

        assert result == TranscriptionResult(text="Hello world", language="en")

    @respx.mock
    def test_server_error_raises(self, tmp_path):
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://whisper:8090/v1/audio/transcriptions").mock(return_value=httpx.Response(500, text="Internal Server Error"))

        with pytest.raises(httpx.HTTPStatusError):
            transcribe_audio(audio_file, server_url="http://whisper:8090", model="large-v3-turbo")

    @respx.mock
    def test_missing_language_defaults_to_unknown(self, tmp_path):
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://whisper:8090/v1/audio/transcriptions").mock(
            return_value=httpx.Response(
                200,
                json={"text": "No language field"},
            )
        )

        result = transcribe_audio(audio_file, server_url="http://whisper:8090", model="large-v3-turbo")

        assert result.language == "unknown"
        assert result.text == "No language field"
