from __future__ import annotations

__all__ = ["FakeEntry", "FakeFeed", "rss_entry", "rss_feed"]


class FakeEntry(dict):
    """Mimics feedparser's FeedParserDict: a dict with attribute access."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            return None


def rss_entry(**kwargs) -> FakeEntry:
    """Build a fake feedparser entry."""
    defaults = {
        "id": "entry-1",
        "title": "Test Post",
        "link": "https://example.com/1",
        "author": "Alice",
        "summary": "Hello world",
        "published": "2025-01-01T00:00:00Z",
    }
    defaults.update(kwargs)
    data = {k: v for k, v in defaults.items() if v is not None}
    return FakeEntry(data)


class FakeFeed:
    """Mimics feedparser's parsed feed object."""

    def __init__(self, entries: list, feed_title: str = "Test Feed"):
        self.entries = entries
        self.bozo = False
        self.bozo_exception: Exception | None = None
        feed_meta = {"title": feed_title}

        class FeedMeta:
            def get(self, key: str, default=None):
                return feed_meta.get(key, default)

        self.feed = FeedMeta()


def rss_feed(entries: list, feed_title: str = "Test Feed") -> FakeFeed:
    return FakeFeed(entries, feed_title)
