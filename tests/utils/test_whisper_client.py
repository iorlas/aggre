"""Tests for whisper transcription HTTP client."""

from __future__ import annotations

from collections import Counter

import httpx
import pytest
import respx

from aggre.utils.whisper_client import (
    Endpoint,
    EndpointBusyError,
    TranscriptionResult,
    _endpoint_slot,
    _semaphores,
    _weighted_shuffle,
    parse_endpoints,
    transcribe_audio,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_semaphores():
    """Clear global semaphore state between tests."""
    _semaphores.clear()
    yield
    _semaphores.clear()


def _ep(
    url: str = "http://test:8090",
    weight: int = 1,
    api_format: str = "whisper-cpp",
    name: str = "test",
    max_concurrent: int = 1,
) -> Endpoint:
    return Endpoint(url=url, weight=weight, api_format=api_format, name=name, max_concurrent=max_concurrent)


class TestParseEndpoints:
    def test_parse_endpoints_full_format(self) -> None:
        raw = "http://host.docker.internal:8090:2:whisper-cpp:macbook-whisper:2,http://zep:8000:10:openai:zep-speaches:10"
        result = parse_endpoints(raw)
        assert result == [
            Endpoint(
                url="http://host.docker.internal:8090",
                weight=2,
                api_format="whisper-cpp",
                name="macbook-whisper",
                max_concurrent=2,
            ),
            Endpoint(
                url="http://zep:8000",
                weight=10,
                api_format="openai",
                name="zep-speaches",
                max_concurrent=10,
            ),
        ]

    def test_parse_endpoints_default_api(self) -> None:
        """When api format is omitted, defaults to whisper-cpp."""
        raw = "http://localhost:8090:5"
        result = parse_endpoints(raw)
        assert result == [
            Endpoint(
                url="http://localhost:8090",
                weight=5,
                api_format="whisper-cpp",
                name="whisper-cpp",
                max_concurrent=5,
            ),
        ]

    def test_parse_endpoints_with_api_no_name(self) -> None:
        raw = "http://host.docker.internal:8090:3:whisper-cpp"
        result = parse_endpoints(raw)
        assert result == [
            Endpoint(
                url="http://host.docker.internal:8090",
                weight=3,
                api_format="whisper-cpp",
                name="whisper-cpp",
                max_concurrent=3,
            ),
        ]

    def test_parse_endpoints_without_port(self) -> None:
        raw = "http://myhost:3:openai"
        result = parse_endpoints(raw)
        assert result == [
            Endpoint(url="http://myhost", weight=3, api_format="openai", name="openai", max_concurrent=3),
        ]

    def test_parse_endpoints_empty(self) -> None:
        assert parse_endpoints("") == []
        assert parse_endpoints("  ") == []

    def test_parse_endpoints_name_and_max_concurrent(self) -> None:
        raw = "http://myhost:8090:5:openai:my-server:3"
        result = parse_endpoints(raw)
        assert result == [
            Endpoint(url="http://myhost:8090", weight=5, api_format="openai", name="my-server", max_concurrent=3),
        ]


class TestWeightedShuffle:
    def test_weighted_shuffle_respects_weights(self) -> None:
        """Higher weight should appear first more often over many runs."""
        endpoints = [
            _ep(url="http://low:8000", weight=1, name="low"),
            _ep(url="http://high:8000", weight=100, api_format="openai", name="high"),
        ]
        first_counts: Counter[str] = Counter()
        for _ in range(500):
            shuffled = _weighted_shuffle(endpoints)
            first_counts[shuffled[0].url] += 1

        # The high-weight endpoint should appear first much more often
        assert first_counts["http://high:8000"] > first_counts["http://low:8000"]
        assert first_counts["http://high:8000"] > 400

    def test_weighted_shuffle_returns_all(self) -> None:
        endpoints = [
            _ep(url="http://a:8000", weight=5, name="a"),
            _ep(url="http://b:8000", weight=5, api_format="openai", name="b"),
            _ep(url="http://c:8000", weight=5, name="c"),
        ]
        shuffled = _weighted_shuffle(endpoints)
        urls = {ep.url for ep in shuffled}
        assert urls == {"http://a:8000", "http://b:8000", "http://c:8000"}


class TestEndpointSlot:
    def test_acquire_and_release(self) -> None:
        ep = _ep(max_concurrent=1, name="slot-test")
        with _endpoint_slot(ep):
            # Slot is held — trying again should fail
            with pytest.raises(EndpointBusyError):
                with _endpoint_slot(ep):
                    pass  # pragma: no cover
        # After release, should work again
        with _endpoint_slot(ep):
            pass

    def test_multiple_slots(self) -> None:
        ep = _ep(max_concurrent=2, name="multi-slot")
        with _endpoint_slot(ep), _endpoint_slot(ep):
            # Both slots taken, third should fail
            with pytest.raises(EndpointBusyError):
                with _endpoint_slot(ep):
                    pass  # pragma: no cover


class TestTranscribeAudio:
    @respx.mock
    def test_successful_transcription(self, tmp_path) -> None:
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://whisper:8090/inference").mock(
            return_value=httpx.Response(
                200,
                json={"text": " Hello world ", "detected_language": "english"},
            )
        )

        ep = _ep(url="http://whisper:8090", name="test-whisper")
        result = transcribe_audio(audio_file, endpoints=[ep], model="large-v3-turbo")

        assert result == TranscriptionResult(text="Hello world", language="english", server_name="test-whisper")

    @respx.mock
    def test_server_error_raises(self, tmp_path) -> None:
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://whisper:8090/inference").mock(return_value=httpx.Response(500, text="Internal Server Error"))

        ep = _ep(url="http://whisper:8090", name="err-whisper")
        with pytest.raises(httpx.HTTPStatusError):
            transcribe_audio(audio_file, endpoints=[ep], model="large-v3-turbo")

    @respx.mock
    def test_missing_language_defaults_to_unknown(self, tmp_path) -> None:
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://whisper:8090/inference").mock(
            return_value=httpx.Response(
                200,
                json={"text": "No language field"},
            )
        )

        ep = _ep(url="http://whisper:8090", name="test-whisper")
        result = transcribe_audio(audio_file, endpoints=[ep], model="large-v3-turbo")

        assert result.language == "unknown"
        assert result.text == "No language field"

    def test_no_endpoints_raises_value_error(self, tmp_path) -> None:
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        with pytest.raises(ValueError, match="No whisper endpoints configured"):
            transcribe_audio(audio_file, endpoints=[], model="large-v3-turbo")


class TestMultiEndpoint:
    @respx.mock
    def test_fallthrough_on_connect_error(self, tmp_path) -> None:
        """First endpoint fails with ConnectError, second succeeds."""
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://down:8090/inference").mock(side_effect=httpx.ConnectError("refused"))
        respx.post("http://up:8000/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "Success from backup", "language": "en"})
        )

        endpoints = [
            _ep(url="http://down:8090", weight=100, name="down"),
            _ep(url="http://up:8000", weight=1, api_format="openai", name="up"),
        ]
        result = transcribe_audio(audio_file, endpoints=endpoints, model="large-v3-turbo")
        assert result.text == "Success from backup"
        assert result.server_name == "up"

    @respx.mock
    def test_all_endpoints_fail_raises_connection_error(self, tmp_path) -> None:
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://a:8090/inference").mock(side_effect=httpx.ConnectError("refused"))
        respx.post("http://b:8000/v1/audio/transcriptions").mock(side_effect=httpx.ConnectTimeout("timeout"))

        endpoints = [
            _ep(url="http://a:8090", weight=5, name="a"),
            _ep(url="http://b:8000", weight=5, api_format="openai", name="b"),
        ]
        with pytest.raises(ConnectionError, match="All 2 whisper endpoints failed"):
            transcribe_audio(audio_file, endpoints=endpoints, model="large-v3-turbo")

    @respx.mock
    def test_server_error_does_not_fallover(self, tmp_path) -> None:
        """500 error raises HTTPStatusError, doesn't try next endpoint."""
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://err:8090/inference").mock(return_value=httpx.Response(500, text="Internal Server Error"))
        backup_route = respx.post("http://backup:8000/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "Should not reach", "language": "en"})
        )

        endpoints = [
            _ep(url="http://err:8090", weight=1000, name="err"),
            _ep(url="http://backup:8000", weight=1, api_format="openai", name="backup"),
        ]
        with pytest.raises(httpx.HTTPStatusError):
            transcribe_audio(audio_file, endpoints=endpoints, model="large-v3-turbo")

        assert not backup_route.called

    @respx.mock
    def test_openai_api_format(self, tmp_path) -> None:
        """Verify correct URL and fields for openai format."""
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        route = respx.post("http://speaches:8000/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": " Transcribed ", "language": "en"})
        )

        endpoints = [_ep(url="http://speaches:8000", weight=10, api_format="openai", name="speaches")]
        result = transcribe_audio(audio_file, endpoints=endpoints, model="large-v3-turbo")

        assert result.text == "Transcribed"
        assert result.server_name == "speaches"
        assert route.called
        request = route.calls[0].request
        assert "/v1/audio/transcriptions" in str(request.url)

    @respx.mock
    def test_whisper_cpp_api_format(self, tmp_path) -> None:
        """Verify correct URL and fields for whisper-cpp format."""
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        route = respx.post("http://whisper:8090/inference").mock(
            return_value=httpx.Response(200, json={"text": " Transcribed ", "detected_language": "de"})
        )

        endpoints = [_ep(url="http://whisper:8090", weight=10, name="local-whisper")]
        result = transcribe_audio(audio_file, endpoints=endpoints, model="large-v3-turbo")

        assert result.text == "Transcribed"
        assert result.language == "de"
        assert result.server_name == "local-whisper"
        assert route.called
        request = route.calls[0].request
        assert "/inference" in str(request.url)

    @respx.mock
    def test_endpoint_busy_fallthrough(self, tmp_path) -> None:
        """When first endpoint is at capacity, falls through to second."""
        audio_file = tmp_path / "audio.opus"
        audio_file.write_bytes(b"fake audio data")

        respx.post("http://backup:8000/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={"text": "From backup", "language": "en"})
        )

        busy_ep = _ep(url="http://busy:8090", weight=1000, name="busy", max_concurrent=1)
        backup_ep = _ep(url="http://backup:8000", weight=1, api_format="openai", name="backup")

        # Exhaust the busy endpoint's semaphore
        _semaphores.setdefault(busy_ep.name, __import__("threading").Semaphore(busy_ep.max_concurrent))
        _semaphores[busy_ep.name].acquire()

        result = transcribe_audio(audio_file, endpoints=[busy_ep, backup_ep], model="large-v3-turbo")
        assert result.text == "From backup"
        assert result.server_name == "backup"

        # Clean up
        _semaphores[busy_ep.name].release()
