from __future__ import annotations

from tests.factories.rss import FakeEntry

__all__ = ["arxiv_entry"]


def arxiv_entry(**kwargs) -> FakeEntry:
    """Build a fake feedparser entry for ArXiv."""
    defaults = {
        "link": "https://arxiv.org/abs/2602.23360v1",
        "title": "Test Paper: A Novel Approach",
        "author": "Alice Researcher",
        "summary": "We present a novel approach to testing.",
        "published": "2025-02-15T00:00:00Z",
        "tags": [{"term": "cs.AI"}, {"term": "cs.CL"}],
    }
    defaults.update(kwargs)
    data = {k: v for k, v in defaults.items() if v is not None}
    return FakeEntry(data)
