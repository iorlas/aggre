"""Base collector protocol."""

from __future__ import annotations

from typing import Protocol

import sqlalchemy as sa
import structlog

from aggre.config import AppConfig


class Collector(Protocol):
    """Protocol that all collectors must implement."""

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        """Fetch new items from the source. Returns count of new items stored."""
        ...
