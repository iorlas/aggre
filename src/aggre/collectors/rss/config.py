"""RSS collector configuration."""

from __future__ import annotations

from pydantic import BaseModel

from aggre.collectors.source_config import SourceBase


class RssSource(SourceBase):
    name: str
    url: str


class RssConfig(BaseModel):
    fetch_limit: int = 50
    init_fetch_limit: int = 50
    sources: list[RssSource] = []
