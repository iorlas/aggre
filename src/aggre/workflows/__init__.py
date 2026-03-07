"""Hatchet workflow orchestration for Aggre."""

from __future__ import annotations

from hatchet_sdk import Hatchet

_hatchet: Hatchet | None = None


def get_hatchet() -> Hatchet:
    """Return the shared Hatchet client, creating it on first call."""
    global _hatchet  # noqa: PLW0603
    if _hatchet is None:
        _hatchet = Hatchet()
    return _hatchet


def start_worker() -> None:  # pragma: no cover — entry point
    """Start the Hatchet worker with all registered workflows."""
    from aggre.workflows.collection import register as reg_collection
    from aggre.workflows.comments import register as reg_comments
    from aggre.workflows.discussion_search import register as reg_discussion_search
    from aggre.workflows.reprocess import register as reg_reprocess
    from aggre.workflows.transcription import register as reg_transcription
    from aggre.workflows.webpage import register as reg_webpage

    h = get_hatchet()
    reg_collection(h)
    reg_comments(h)
    reg_discussion_search(h)
    reg_reprocess(h)
    reg_transcription(h)
    reg_webpage(h)

    worker = h.worker("aggre-worker", slots=20)
    worker.start()
