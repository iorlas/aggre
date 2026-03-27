"""YouTube collector configuration."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from aggre.collectors.source_config import SourceBase


class TranscribePolicy(StrEnum):
    always = "always"
    keyword = "keyword"
    never = "never"


class YoutubeSource(SourceBase):
    channel_id: str
    name: str
    transcribe: TranscribePolicy = TranscribePolicy.always
    keywords: list[str] = []
    max_duration_minutes: int | None = None
    fetch_interval_hours: int | None = None


class YoutubeConfig(BaseModel):
    fetch_limit: int = 10
    init_fetch_limit: int = 100
    sources: list[YoutubeSource] = []
