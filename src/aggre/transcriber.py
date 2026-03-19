"""Transcription abstraction — pluggable backends with priority-based fallback."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Sequence
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str
    transcribed_by: str


class Transcriber(Protocol):
    def __call__(self, audio: bytes, format_hint: str = "opus") -> TranscriptResult: ...


class QuotaExceededError(Exception):
    """Backend has exhausted its quota (e.g. Modal free credits)."""


class AllTranscribersFailedError(Exception):
    """Every configured backend failed with a fallback-eligible error."""


def transcribe_with_fallback(
    transcribers: Sequence[Transcriber],
    audio: bytes,
    format_hint: str = "opus",
) -> TranscriptResult:
    """Try each transcriber in order. Fall back on quota/connection errors only."""
    last_error: Exception | None = None
    for transcriber in transcribers:
        try:
            return transcriber(audio, format_hint)
        except (QuotaExceededError, ConnectionError) as exc:
            logger.warning("transcriber.fallback backend=%s error=%s", type(transcriber).__name__, exc)
            last_error = exc
    raise AllTranscribersFailedError(
        f"All {len(transcribers)} transcription backends failed"
    ) from last_error
