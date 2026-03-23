from __future__ import annotations

__all__ = ["reddit_comment", "reddit_comment_listing", "reddit_listing", "reddit_post"]


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
