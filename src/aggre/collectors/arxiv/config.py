"""ArXiv collector configuration."""

from __future__ import annotations

from pydantic import BaseModel

from aggre.collectors.source_config import SourceBase


class ArxivSource(SourceBase):
    name: str  # e.g., "ArXiv CS.AI"
    category: str  # e.g., "cs.AI", "cs.CL"


class ArxivConfig(BaseModel):
    fetch_limit: int = 100
    init_fetch_limit: int = 100
    sources: list[ArxivSource] = []
