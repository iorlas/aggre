from __future__ import annotations

from enum import StrEnum


class StageStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class Stage(StrEnum):
    DOWNLOAD = "download"
    EXTRACT = "extract"
    TRANSCRIBE = "transcribe"
    DISCUSSION_SEARCH = "discussion_search"
    COMMENTS = "comments"


MAX_RETRIES: dict[Stage, int] = {
    Stage.DOWNLOAD: 3,
    Stage.EXTRACT: 2,
    Stage.TRANSCRIBE: 2,
    Stage.DISCUSSION_SEARCH: 3,
    Stage.COMMENTS: 3,
}

COOLDOWN_SECONDS: dict[Stage, int] = {
    Stage.DOWNLOAD: 600,
    Stage.EXTRACT: 60,
    Stage.TRANSCRIBE: 600,
    Stage.DISCUSSION_SEARCH: 600,
    Stage.COMMENTS: 600,
}
