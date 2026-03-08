"""Thin HTTP client for whisper.cpp server (/inference endpoint)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx


@dataclasses.dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str


def transcribe_audio(
    audio_path: Path,
    *,
    server_url: str,
    model: str,
    timeout: float = 300.0,
) -> TranscriptionResult:
    """POST audio to whisper.cpp server, return transcription result."""
    with audio_path.open("rb") as f:
        response = httpx.post(
            f"{server_url}/inference",
            files={"file": (audio_path.name, f, "audio/ogg")},
            data={"model": model, "response_format": "verbose_json", "temperature": "0.0"},
            timeout=timeout,
        )
    response.raise_for_status()
    body = response.json()
    # whisper.cpp uses "detected_language" in verbose_json; fall back to "language" for compat
    language = body.get("detected_language") or body.get("language") or "unknown"
    return TranscriptionResult(
        text=body["text"].strip(),
        language=language,
    )
