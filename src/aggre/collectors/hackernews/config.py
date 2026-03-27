"""Hacker News collector configuration."""

from __future__ import annotations

from pydantic import BaseModel

from aggre.collectors.source_config import SourceBase


class HackernewsSource(SourceBase):
    name: str = "Hacker News"


class HackernewsConfig(BaseModel):
    fetch_limit: int = 1000
    init_fetch_limit: int = 200
    sources: list[HackernewsSource] = []
