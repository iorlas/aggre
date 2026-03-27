"""Reddit collector configuration."""

from __future__ import annotations

from pydantic import BaseModel

from aggre.collectors.source_config import SourceBase


class RedditSource(SourceBase):
    subreddit: str


class RedditConfig(BaseModel):
    fetch_limit: int = 100
    init_fetch_limit: int = 500
    sources: list[RedditSource] = []
