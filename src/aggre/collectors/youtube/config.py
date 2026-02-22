"""YouTube collector configuration."""

from __future__ import annotations

from pydantic import BaseModel


class YoutubeSource(BaseModel):
    channel_id: str
    name: str


class YoutubeConfig(BaseModel):
    fetch_limit: int = 10
    init_fetch_limit: int = 100
    sources: list[YoutubeSource] = []
