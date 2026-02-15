"""YAML config loading with env var overrides via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGGRE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg2://localhost/aggre"
    log_dir: str = "./data/logs"
    youtube_temp_dir: str = "./data/tmp/videos"
    whisper_model: str = "large-v3"
    whisper_model_cache: str = "./data/models"
    reddit_rate_limit: float = 3.0
    hn_rate_limit: float = 1.0
    lobsters_rate_limit: float = 2.0
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session: str = ""  # StringSession base64 string
    telegram_rate_limit: float = 2.0  # seconds between channel fetches
    fetch_limit: int = 100


class RssSource(BaseModel):
    name: str
    url: str


class RedditSource(BaseModel):
    subreddit: str


class YoutubeSource(BaseModel):
    channel_id: str
    name: str


class HackernewsSource(BaseModel):
    name: str = "Hacker News"


class LobstersSource(BaseModel):
    name: str = "Lobsters"
    tags: list[str] = []


class HuggingfaceSource(BaseModel):
    name: str = "HuggingFace Papers"


class TelegramSource(BaseModel):
    username: str  # channel @handle without @ (e.g. "durov")
    name: str  # display name for Source table


class AppConfig(BaseModel):
    rss: list[RssSource] = []
    reddit: list[RedditSource] = []
    youtube: list[YoutubeSource] = []
    hackernews: list[HackernewsSource] = []
    lobsters: list[LobstersSource] = []
    huggingface: list[HuggingfaceSource] = []
    telegram: list[TelegramSource] = []
    settings: Settings = Settings()


def load_config(config_path: str = "config.yaml") -> AppConfig:
    """Load config from YAML file; settings come from env vars via pydantic-settings."""
    data: dict = {}
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}

    # Remove any leftover settings block from YAML — env vars are the source of truth
    data.pop("settings", None)

    return AppConfig(**data, settings=Settings())
