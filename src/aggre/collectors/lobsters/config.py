"""Lobsters collector configuration."""

from __future__ import annotations

from pydantic import BaseModel


class LobstersSource(BaseModel):
    name: str = "Lobsters"
    tags: list[str] = []


class LobstersConfig(BaseModel):
    fetch_limit: int = 50
    init_fetch_limit: int = 200
    sources: list[LobstersSource] = []
