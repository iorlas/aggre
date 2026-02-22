"""HuggingFace collector configuration."""

from __future__ import annotations

from pydantic import BaseModel


class HuggingfaceSource(BaseModel):
    name: str = "HuggingFace Papers"


class HuggingfaceConfig(BaseModel):
    fetch_limit: int = 100
    init_fetch_limit: int = 100
    sources: list[HuggingfaceSource] = []
