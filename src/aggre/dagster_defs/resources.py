"""Dagster resources for Aggre pipeline."""

from __future__ import annotations

import dagster as dg
import sqlalchemy as sa

from aggre.config import AppConfig, load_config
from aggre.utils.db import get_engine


class DatabaseResource(dg.ConfigurableResource):
    """SQLAlchemy engine resource."""

    database_url: str = dg.EnvVar("AGGRE_DATABASE_URL")

    def get_engine(self) -> sa.engine.Engine:
        return get_engine(self.database_url)


class AppConfigResource(dg.ConfigurableResource):
    """Application config resource — wraps load_config() for Dagster DI."""

    config_path: str = "config.yaml"

    def get_config(self) -> AppConfig:
        return load_config(self.config_path)
