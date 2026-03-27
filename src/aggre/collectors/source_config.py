"""Shared base configuration for all collector sources."""

from __future__ import annotations

from pydantic import BaseModel


class SourceBase(BaseModel):
    """Common fields shared by all source configurations.

    Every collector's per-source model inherits from this to get
    cross-cutting fields (categories) without duplication.
    """

    categories: list[str] = []
