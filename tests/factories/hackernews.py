from __future__ import annotations

__all__ = ["hn_comment_child", "hn_hit", "hn_item_response", "hn_search_response"]


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
