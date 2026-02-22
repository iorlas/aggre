"""Dagster resources for Aggre pipeline."""

from __future__ import annotations

import dagster as dg
import sqlalchemy as sa

from aggre.db import get_engine


class DatabaseResource(dg.ConfigurableResource):
    """SQLAlchemy engine resource."""

    database_url: str = dg.EnvVar("AGGRE_DATABASE_URL")

    def get_engine(self) -> sa.engine.Engine:
        return get_engine(self.database_url)
