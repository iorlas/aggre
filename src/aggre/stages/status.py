from __future__ import annotations

from enum import StrEnum


class StageStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class Stage(StrEnum):
    DOWNLOAD = "download"
    EXTRACT = "extract"
    TRANSCRIBE = "transcribe"
    ENRICH = "enrich"
    COMMENTS = "comments"


MAX_RETRIES: dict[Stage, int] = {
    Stage.DOWNLOAD: 3,
    Stage.EXTRACT: 2,
    Stage.TRANSCRIBE: 2,
    Stage.ENRICH: 3,
    Stage.COMMENTS: 3,
}

COOLDOWN_SECONDS: dict[Stage, int] = {
    Stage.DOWNLOAD: 600,
    Stage.EXTRACT: 60,
    Stage.TRANSCRIBE: 600,
    Stage.ENRICH: 600,
    Stage.COMMENTS: 600,
}
