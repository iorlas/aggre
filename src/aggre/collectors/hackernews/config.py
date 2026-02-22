"""Hacker News collector configuration."""

from __future__ import annotations

from pydantic import BaseModel


class HackernewsSource(BaseModel):
    name: str = "Hacker News"


class HackernewsConfig(BaseModel):
    fetch_limit: int = 100
    init_fetch_limit: int = 200
    sources: list[HackernewsSource] = []
