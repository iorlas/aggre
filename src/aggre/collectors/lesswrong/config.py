"""LessWrong collector configuration."""

from __future__ import annotations

from pydantic import BaseModel

from aggre.collectors.source_config import SourceBase


class LesswrongSource(SourceBase):
    name: str  # e.g., "LessWrong Frontpage"
    view: str = "new"  # "new", "magic", "top"
    min_karma: int = 10  # minimum baseScore to ingest
    alignment_forum: bool = False  # filter to AF posts only


class LesswrongConfig(BaseModel):
    fetch_limit: int = 50
    init_fetch_limit: int = 50
    sources: list[LesswrongSource] = []
