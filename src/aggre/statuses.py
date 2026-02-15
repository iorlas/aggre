"""Status enumerations for pipeline stages."""

from __future__ import annotations

from enum import StrEnum


class FetchStatus(StrEnum):
    """Content fetch lifecycle."""
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    FETCHED = "fetched"
    SKIPPED = "skipped"
    FAILED = "failed"


class TranscriptionStatus(StrEnum):
    """YouTube transcription lifecycle."""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"


class CommentsStatus(StrEnum):
    """Comment collection lifecycle."""
    PENDING = "pending"
    DONE = "done"
