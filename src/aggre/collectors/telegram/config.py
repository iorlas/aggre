"""Telegram collector configuration."""

from __future__ import annotations

from pydantic import BaseModel


class TelegramSource(BaseModel):
    username: str  # channel @handle without @ (e.g. "durov")
    name: str  # display name for Source table


class TelegramConfig(BaseModel):
    fetch_limit: int = 100
    init_fetch_limit: int = 1000
    sources: list[TelegramSource] = []
