"""GitHub Trending collector configuration."""

from __future__ import annotations

from pydantic import BaseModel


class GithubTrendingConfig(BaseModel):
    """No user-configurable fields — periods are hardcoded in the collector."""

    pass
