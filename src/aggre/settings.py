"""Global settings loaded from environment variables via pydantic-settings."""

from __future__ import annotations

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
    whisper_model: str = "large-v3-turbo"
    whisper_model_cache: str = "./data/models"
    proxy_url: str = ""
    # Rate limits (operational, stay as env vars)
    reddit_rate_limit: float = 3.0
    hn_rate_limit: float = 1.0
    lobsters_rate_limit: float = 2.0
    telegram_rate_limit: float = 2.0
    # Telegram credentials (secrets, must be env vars)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session: str = ""  # StringSession base64 string
