from __future__ import annotations

__all__ = ["hf_paper"]


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
