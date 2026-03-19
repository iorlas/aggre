"""Transcription abstraction — pluggable backends with priority-based fallback."""

from __future__ import annotations

import dataclasses
from typing import Protocol


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
