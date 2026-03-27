"""Lobsters collector configuration."""

from __future__ import annotations

from pydantic import BaseModel

from aggre.collectors.source_config import SourceBase


class LobstersSource(SourceBase):
    name: str = "Lobsters"
    tags: list[str] = []


class LobstersConfig(BaseModel):
    fetch_limit: int = 50
    init_fetch_limit: int = 200
    pages: int = 4
    sources: list[LobstersSource] = []
