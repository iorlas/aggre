"""Pydantic data contracts for Hatchet workflow inputs and outputs."""

from __future__ import annotations

from pydantic import BaseModel

# -- Inputs --


class RssSourceInput(BaseModel):
    """Input for per-feed RSS child workflow."""

    name: str
    url: str


class ItemEvent(BaseModel):
    """Per-item event payload for downstream processing workflows.

    Emitted by collectors after each discussion is processed.
    Subscribers query DB for full data — this only carries IDs + concurrency keys.
    """

    content_id: int
    discussion_id: int
    source: str  # "hackernews", "reddit", etc. — for concurrency grouping
    domain: str | None = None  # content domain — for concurrency grouping


# -- Outputs --


class TaskResult(BaseModel):
    """Common result for batch-processing tasks."""

    succeeded: int = 0
    failed: int = 0
    total: int = 0


class CollectResult(TaskResult):
    """Collection result with source identifier."""

    source: str = ""
    event_errors: int = 0  # Items processed but failed to emit downstream event


class StepOutput(BaseModel):
    """Per-item task output. Visible as JSON in Hatchet UI."""

    status: str  # "downloaded", "cached", "skipped", "transcribed", etc.
    reason: str | None = None  # Short reason code for skips: "already_done", "not_found", etc.
    url: str | None = None  # What URL was processed
    detail: dict[str, str] | None = None  # Extras: transcriber, language, duration, counts
