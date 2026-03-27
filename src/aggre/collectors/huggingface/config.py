"""HuggingFace collector configuration."""

from __future__ import annotations

from pydantic import BaseModel

from aggre.collectors.source_config import SourceBase


class HuggingfaceSource(SourceBase):
    name: str = "HuggingFace Papers"


class HuggingfaceConfig(BaseModel):
    fetch_limit: int = 100
    init_fetch_limit: int = 100
    sources: list[HuggingfaceSource] = []
