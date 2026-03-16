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
    # Structural signal: True when the collector already provided the content text
    # (e.g. Reddit self-posts, Ask HN text, Telegram messages). Webpage and transcription
    # workflows filter on this to avoid queueing for content that has no external page.
    # This is NOT processing state — it's a property of the content type.
    text_provided: bool = False


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
    events_skipped: int = 0  # Items skipped because already fully processed (dedup)


class StepOutput(BaseModel):
    """Per-item task output. Visible as JSON in Hatchet UI."""

    status: str  # "downloaded", "cached", "skipped", "transcribed", etc.
    reason: str | None = None  # Short reason code for skips: "already_done", "not_found", etc.
    url: str | None = None  # What URL was processed
    detail: dict[str, str] | None = None  # Extras: transcriber, language, duration, counts
