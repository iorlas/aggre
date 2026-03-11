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


class DownloadResult(TaskResult):
    """Download result with cache/skip tracking."""

    cached: int = 0
    skipped: int = 0


class StepOutput(BaseModel):
    """Flexible output model for individual workflow task steps."""

    status: str = ""
    reason: str = ""
