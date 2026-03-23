from __future__ import annotations

__all__ = ["lobsters_comment", "lobsters_story", "lobsters_story_detail"]


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
