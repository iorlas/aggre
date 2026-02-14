"""YAML config loading with env var overrides."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel


class Settings(BaseModel):
    db_path: str = "./data/aggre.db"
    log_dir: str = "./data/logs"
    youtube_temp_dir: str = "./data/tmp/videos"
    whisper_model: str = "large-v3"
    whisper_model_cache: str = "./data/models"
    reddit_rate_limit: float = 3.0
    fetch_limit: int = 100


class RssSource(BaseModel):
    name: str
    url: str


class RedditSource(BaseModel):
    subreddit: str


class YoutubeSource(BaseModel):
    channel_id: str
    name: str


class AppConfig(BaseModel):
    rss: list[RssSource] = []
    reddit: list[RedditSource] = []
    youtube: list[YoutubeSource] = []
    settings: Settings = Settings()


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """Load config from YAML file, then apply env var overrides."""
    load_dotenv()

    data: dict = {}
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}

    config = AppConfig(**data)

    # Apply env var overrides for settings
    env_map = {
        "AGGRE_DB_PATH": "db_path",
        "AGGRE_LOG_DIR": "log_dir",
        "AGGRE_YOUTUBE_TEMP_DIR": "youtube_temp_dir",
        "AGGRE_WHISPER_MODEL": "whisper_model",
        "AGGRE_WHISPER_MODEL_CACHE": "whisper_model_cache",
        "AGGRE_REDDIT_RATE_LIMIT": "reddit_rate_limit",
        "AGGRE_FETCH_LIMIT": "fetch_limit",
    }

    for env_var, field_name in env_map.items():
        val = os.environ.get(env_var)
        if val is not None:
            field_info = Settings.model_fields[field_name]
            if field_info.annotation is float:
                setattr(config.settings, field_name, float(val))
            elif field_info.annotation is int:
                setattr(config.settings, field_name, int(val))
            else:
                setattr(config.settings, field_name, val)

    return config
