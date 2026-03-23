from __future__ import annotations

__all__ = ["lesswrong_graphql_response", "lesswrong_post"]


def lesswrong_post(
    post_id: str = "abc123lw",
    title: str = "Test LW Post",
    slug: str = "test-lw-post",
    base_score: int = 42,
    vote_count: int = 50,
    comment_count: int = 5,
    af: bool = False,
    url: str | None = None,
    page_url: str | None = None,
    posted_at: str = "2025-01-15T00:00:00.000Z",
    tags: list[dict] | None = None,
    user: dict | None = None,
) -> dict:
    return {
        "_id": post_id,
        "title": title,
        "slug": slug,
        "pageUrl": page_url or f"https://www.lesswrong.com/posts/{post_id}/{slug}",
        "postedAt": posted_at,
        "baseScore": base_score,
        "voteCount": vote_count,
        "commentCount": comment_count,
        "af": af,
        "url": url,
        "user": user or {"displayName": "Test Author"},
        "tags": tags if tags is not None else [{"name": "rationality"}, {"name": "AI"}],
    }


def lesswrong_graphql_response(*posts: dict) -> dict:
    return {"data": {"posts": {"results": list(posts)}}}
