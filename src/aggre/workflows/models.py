"""Pydantic data contracts for Hatchet workflow inputs and outputs."""

from __future__ import annotations

from pydantic import BaseModel

# -- Inputs --


class RssSourceInput(BaseModel):
    """Input for per-feed RSS child workflow."""

    name: str
    url: str


class SilverContentRef(BaseModel):
    """Compact reference to a silver_content row for event dispatch.

    Mirrors DB columns — no derived fields.
    Workflows must re-fetch from DB for processing.

    Note: ``text_provided`` is defense-in-depth only. Due to Layer 1 emission-time
    dedup, any event that reaches construction will have text_provided=False.
    CEL filters on this field guard against future relaxation of Layer 1.
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
