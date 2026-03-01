"""Shared test data factories — single source of truth for ALL test data.

Replaces ~15 duplicate helper functions across 9+ test files.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.collectors.hackernews.config import HackernewsConfig
from aggre.collectors.huggingface.config import HuggingfaceConfig
from aggre.collectors.lobsters.config import LobstersConfig
from aggre.collectors.reddit.config import RedditConfig
from aggre.collectors.rss.config import RssConfig
from aggre.collectors.telegram.config import TelegramConfig
from aggre.collectors.youtube.config import YoutubeConfig
from aggre.config import AppConfig
from aggre.db import SilverContent, SilverObservation, Source
from aggre.settings import Settings

# ===========================================================================
# DB seeders
# ===========================================================================


def seed_content(
    engine: sa.engine.Engine,
    url: str,
    *,
    domain: str | None = None,
    text: str | None = None,
) -> int:
    """Insert a SilverContent row. Returns the row id."""
    with engine.begin() as conn:
        stmt = pg_insert(SilverContent).values(
            canonical_url=url,
            domain=domain,
            text=text,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
        result = conn.execute(stmt)
        return result.inserted_primary_key[0]


def seed_observation(
    engine: sa.engine.Engine,
    *,
    source_type: str,
    external_id: str,
    content_id: int | None = None,
    title: str | None = None,
    author: str | None = None,
    url: str | None = None,
    content_text: str | None = None,
    published_at: str | None = None,
    comments_json: str | None = None,
    score: int | None = None,
    comment_count: int | None = None,
    source_id: int | None = None,
) -> int:
    """Insert a SilverObservation row. Returns the row id."""
    with engine.begin() as conn:
        stmt = pg_insert(SilverObservation).values(
            source_type=source_type,
            external_id=external_id,
            content_id=content_id,
            title=title,
            author=author,
            url=url,
            content_text=content_text,
            published_at=published_at,
            comments_json=comments_json,
            score=score,
            comment_count=comment_count,
            source_id=source_id,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["source_type", "external_id"])
        result = conn.execute(stmt)
        return result.inserted_primary_key[0]


def seed_source(
    engine: sa.engine.Engine,
    *,
    source_type: str,
    name: str,
    config: str = "{}",
) -> int:
    """Insert a Source row. Returns the row id."""
    with engine.begin() as conn:
        result = conn.execute(sa.insert(Source).values(type=source_type, name=name, config=config))
        return result.inserted_primary_key[0]


# ===========================================================================
# HackerNews response builders
# ===========================================================================


def hn_hit(
    object_id: str = "12345",
    title: str = "Test Story",
    author: str = "pg",
    url: str | None = "https://example.com/article",
    points: int = 100,
    num_comments: int = 25,
    created_at: str = "2024-01-15T12:00:00.000Z",
    story_text: str | None = None,
) -> dict:
    """Build a single HN Algolia search hit."""
    hit = {
        "objectID": object_id,
        "title": title,
        "author": author,
        "url": url,
        "points": points,
        "num_comments": num_comments,
        "created_at": created_at,
    }
    if story_text is not None:
        hit["story_text"] = story_text
    return hit


def hn_search_response(*hits: dict) -> dict:
    return {"hits": list(hits)}


def hn_item_response(object_id: str = "12345", children: list | None = None) -> dict:
    return {"id": int(object_id), "children": children or []}


def hn_comment_child(
    comment_id: int = 100,
    author: str = "commenter",
    text: str = "Great article!",
    points: int = 5,
    parent_id: int = 12345,
    children: list | None = None,
) -> dict:
    return {
        "id": comment_id,
        "author": author,
        "text": text,
        "points": points,
        "parent_id": parent_id,
        "created_at": "2024-01-15T13:00:00.000Z",
        "children": children or [],
    }


# ===========================================================================
# Reddit response builders
# ===========================================================================


def reddit_post(
    post_id: str = "abc123",
    title: str = "Test Post",
    author: str = "testuser",
    subreddit: str = "python",
    selftext: str = "This is the body text",
    score: int = 42,
    num_comments: int = 5,
    flair: str | None = "Discussion",
    url: str | None = None,
    is_self: bool = False,
) -> dict:
    return {
        "kind": "t3",
        "data": {
            "name": f"t3_{post_id}",
            "title": title,
            "author": author,
            "selftext": selftext,
            "permalink": f"/r/{subreddit}/comments/{post_id}/test_post/",
            "created_utc": 1700000000.0,
            "score": score,
            "num_comments": num_comments,
            "link_flair_text": flair,
            "subreddit": subreddit,
            "url": url or f"https://reddit.com/r/{subreddit}/comments/{post_id}/test_post/",
            "is_self": is_self,
        },
    }


def reddit_listing(*posts: dict) -> dict:
    return {"data": {"children": list(posts)}}


def reddit_comment(
    comment_id: str = "com1",
    body: str = "Nice post!",
    author: str = "commenter",
    parent_id: str = "t3_abc123",
    score: int = 10,
    replies: dict | str | None = None,
) -> dict:
    return {
        "kind": "t1",
        "data": {
            "name": f"t1_{comment_id}",
            "author": author,
            "body": body,
            "score": score,
            "parent_id": parent_id,
            "created_utc": 1700001000.0,
            "replies": replies or "",
        },
    }


def reddit_comment_listing(*comments: dict) -> list:
    """Build the [post_listing, comments_listing] structure returned by comment endpoints."""
    post_part = {"data": {"children": [reddit_post()]}}
    comment_part = {"data": {"children": list(comments)}}
    return [post_part, comment_part]


# ===========================================================================
# Lobsters response builders
# ===========================================================================


def lobsters_story(
    short_id: str = "abc123",
    title: str = "Test Story",
    url: str = "https://example.com/article",
    score: int = 10,
    comment_count: int = 3,
    tags: list[str] | None = None,
    submitter_user: str = "testuser",
) -> dict:
    return {
        "short_id": short_id,
        "title": title,
        "url": url,
        "score": score,
        "comment_count": comment_count,
        "tags": tags or ["programming"],
        "submitter_user": submitter_user,
        "created_at": "2024-01-15T12:00:00.000Z",
        "comments_url": f"https://lobste.rs/s/{short_id}",
    }


def lobsters_story_detail(short_id: str = "abc123", comments: list | None = None) -> dict:
    story = lobsters_story(short_id=short_id)
    story["comments"] = comments or []
    return story


def lobsters_comment(
    short_id: str = "com1",
    comment: str = "Great article!",
    username: str = "commenter",
    score: int = 5,
    indent_level: int = 1,
    parent_comment: str | None = None,
) -> dict:
    return {
        "short_id": short_id,
        "comment": comment,
        "commenting_user": {"username": username},
        "score": score,
        "indent_level": indent_level,
        "parent_comment": parent_comment,
        "created_at": "2024-01-15T13:00:00.000Z",
    }


# ===========================================================================
# RSS response builders
# ===========================================================================


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
        feed_meta = {"title": feed_title}

        class FeedMeta:
            def get(self, key: str, default=None):
                return feed_meta.get(key, default)

        self.feed = FeedMeta()


def rss_feed(entries: list, feed_title: str = "Test Feed") -> FakeFeed:
    return FakeFeed(entries, feed_title)


# ===========================================================================
# YouTube response builders
# ===========================================================================


def youtube_entry(
    video_id: str = "vid001",
    title: str = "Test Video",
    url: str | None = None,
    upload_date: str = "20240115",
    duration: int = 600,
    view_count: int = 1000,
) -> dict:
    entry: dict = {
        "id": video_id,
        "title": title,
        "upload_date": upload_date,
        "duration": duration,
        "view_count": view_count,
    }
    if url is not None:
        entry["url"] = url
    else:
        entry["url"] = f"https://www.youtube.com/watch?v={video_id}"
    return entry


# ===========================================================================
# HuggingFace response builders
# ===========================================================================


def hf_paper(
    paper_id: str = "2401.12345",
    title: str = "Test Paper",
    summary: str = "A summary of the paper.",
    upvotes: int = 42,
    num_comments: int = 5,
    authors: list[dict] | None = None,
    github_repo: str | None = "https://github.com/example/repo",
    published_at: str = "2024-01-15T00:00:00.000Z",
) -> dict:
    return {
        "paper": {
            "id": paper_id,
            "title": title,
            "summary": summary,
            "authors": [{"name": "Alice"}, {"name": "Bob"}] if authors is None else authors,
            "publishedAt": published_at,
            "upvotes": upvotes,
            "numComments": num_comments,
            "githubRepo": github_repo,
        },
        "numComments": num_comments,
    }


# ===========================================================================
# Telegram response builders
# ===========================================================================


def telegram_message(
    msg_id: int = 1,
    text: str | None = "Hello world",
    date: datetime | None = None,
    views: int = 100,
    forwards: int = 5,
    media: object | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.date = date or datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    msg.views = views
    msg.forwards = forwards
    msg.media = media
    return msg


def telegram_mock_client(messages_by_username: dict[str, list]) -> AsyncMock:
    """Build a mock TelegramClient that returns configured messages."""
    client = AsyncMock()

    async def get_messages(username: str, limit: int = 100):
        return messages_by_username.get(username, [])

    client.get_messages = AsyncMock(side_effect=get_messages)
    return client


# ===========================================================================
# Config builder
# ===========================================================================


def make_config(
    *,
    hackernews: HackernewsConfig | None = None,
    reddit: RedditConfig | None = None,
    rss: RssConfig | None = None,
    youtube: YoutubeConfig | None = None,
    lobsters: LobstersConfig | None = None,
    huggingface: HuggingfaceConfig | None = None,
    telegram: TelegramConfig | None = None,
    rate_limit: float = 0.0,
    proxy_url: str = "",
    telegram_api_id: int = 0,
    telegram_api_hash: str = "",
    telegram_session: str = "",
) -> AppConfig:
    """Build an AppConfig with defaults suitable for tests."""
    return AppConfig(
        hackernews=hackernews or HackernewsConfig(),
        reddit=reddit or RedditConfig(),
        rss=rss or RssConfig(),
        youtube=youtube or YoutubeConfig(),
        lobsters=lobsters or LobstersConfig(),
        huggingface=huggingface or HuggingfaceConfig(),
        telegram=telegram or TelegramConfig(),
        settings=Settings(
            hn_rate_limit=rate_limit,
            reddit_rate_limit=rate_limit,
            lobsters_rate_limit=rate_limit,
            telegram_rate_limit=rate_limit,
            proxy_url=proxy_url,
            telegram_api_id=telegram_api_id,
            telegram_api_hash=telegram_api_hash,
            telegram_session=telegram_session,
        ),
    )
