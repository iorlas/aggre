"""HTTP client for whisper transcription servers (whisper.cpp and OpenAI-compatible)."""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import random
import threading
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Endpoint:
    url: str
    weight: int
    api_format: str  # "whisper-cpp" or "openai"
    name: str  # e.g. "zep-speaches" — stored as transcribed_by
    max_concurrent: int


@dataclasses.dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    server_name: str  # endpoint name, e.g. "zep-speaches"


class EndpointBusyError(Exception):
    """Raised when an endpoint has no free concurrency slots."""


_semaphores: dict[str, threading.Semaphore] = {}


@contextlib.contextmanager
def _endpoint_slot(ep: Endpoint):
    """Acquire a concurrency slot for this endpoint. Raises EndpointBusy if at capacity."""
    sem = _semaphores.setdefault(ep.name, threading.Semaphore(ep.max_concurrent))
    if not sem.acquire(blocking=False):
        raise EndpointBusyError(ep.name)
    try:
        yield
    finally:
        sem.release()


def parse_endpoints(raw: str) -> list[Endpoint]:
    """Parse endpoint config string into list of Endpoint.

    Format: ``url:weight:api:name:max_concurrent`` per entry, comma-separated.
    - api defaults to "whisper-cpp" if omitted
    - name defaults to api_format if omitted
    - max_concurrent defaults to weight if omitted

    Examples:
      http://host.docker.internal:8090:2:whisper-cpp:macbook-whisper:2
      http://zep:8000:10:openai:zep-speaches:10
    """
    if not raw.strip():
        return []
    endpoints: list[Endpoint] = []
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        # URL has scheme:// so first two parts are scheme + host
        # Strategy: parse from the right for known fields
        # Possible trailing fields: max_concurrent, name, api_format
        # We need to detect which optional fields are present

        # Parse optional trailing fields from the right.
        # max_concurrent is only present when name is also present
        # (name is a non-numeric, non-api-format string).
        max_concurrent_val = None
        name_val = None
        api_format = "whisper-cpp"

        # Check for max_concurrent:name pair at the end
        if parts[-1].isdigit() and len(parts) >= 2 and parts[-2] not in ("whisper-cpp", "openai") and not parts[-2].isdigit():
            max_concurrent_val = int(parts[-1])
            name_val = parts[-2]
            parts = parts[:-2]
        elif not parts[-1].isdigit() and parts[-1] not in ("whisper-cpp", "openai"):
            # name without max_concurrent
            name_val = parts[-1]
            parts = parts[:-1]

        # Now last could be api_format
        if parts[-1] in ("whisper-cpp", "openai"):
            api_format = parts[-1]
            parts = parts[:-1]

        # Last remaining part is weight
        weight = int(parts[-1])
        url = ":".join(parts[:-1])

        if name_val is None:
            name_val = api_format
        if max_concurrent_val is None:
            max_concurrent_val = weight

        endpoints.append(
            Endpoint(
                url=url,
                weight=weight,
                api_format=api_format,
                name=name_val,
                max_concurrent=max_concurrent_val,
            )
        )
    return endpoints


def _weighted_shuffle(endpoints: list[Endpoint]) -> list[Endpoint]:
    """Shuffle endpoints with higher weights more likely to appear first."""
    remaining = list(endpoints)
    result: list[Endpoint] = []
    while remaining:
        weights = [ep.weight for ep in remaining]
        chosen = random.choices(range(len(remaining)), weights=weights, k=1)[0]  # noqa: S311 — weighted shuffle for load balancing, not security
        result.append(remaining.pop(chosen))
    return result


def _call_server(
    audio_path: Path,
    ep: Endpoint,
    model: str,
    timeout: float,
) -> TranscriptionResult:
    """Send audio to a single transcription server and return the result."""
    with audio_path.open("rb") as f:
        if ep.api_format == "openai":
            response = httpx.post(
                f"{ep.url}/v1/audio/transcriptions",
                files={"file": (audio_path.name, f, "audio/ogg")},
                data={"model": model, "response_format": "verbose_json"},
                timeout=timeout,
            )
        else:
            response = httpx.post(
                f"{ep.url}/inference",
                files={"file": (audio_path.name, f, "audio/ogg")},
                data={"model": model, "response_format": "verbose_json", "temperature": "0.0"},
                timeout=timeout,
            )
    response.raise_for_status()
    body = response.json()
    language = body.get("detected_language") or body.get("language") or "unknown"
    return TranscriptionResult(
        text=body["text"].strip(),
        language=language,
        server_name=ep.name,
    )


def transcribe_audio(
    audio_path: Path,
    *,
    endpoints: list[Endpoint],
    model: str,
    timeout: float = 300.0,
) -> TranscriptionResult:
    """POST audio to a whisper transcription server, return transcription result.

    Tries endpoints in weighted-random order. Skips endpoints at capacity (EndpointBusy).
    Connection failures (ConnectError, ConnectTimeout) trigger fallover to the next endpoint.
    Server errors (4xx/5xx) do NOT trigger fallover.
    """
    if not endpoints:
        raise ValueError("No whisper endpoints configured")

    shuffled = _weighted_shuffle(endpoints)
    last_error: Exception | None = None
    for ep in shuffled:
        try:
            with _endpoint_slot(ep):
                return _call_server(audio_path, ep, model, timeout)
        except EndpointBusyError:
            logger.info("whisper_client.endpoint_busy name=%s", ep.name)
            last_error = EndpointBusyError(ep.name)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.warning("whisper_client.connect_failed name=%s url=%s error=%s", ep.name, ep.url, exc)
            last_error = exc
    raise ConnectionError(f"All {len(shuffled)} whisper endpoints failed") from last_error
