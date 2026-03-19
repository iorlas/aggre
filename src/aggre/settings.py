"""Global settings loaded from environment variables via pydantic-settings."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGGRE_",
        env_file=None if "PYTEST_CURRENT_TEST" in os.environ else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://localhost/aggre"
    log_dir: str = "./data/logs"
    youtube_temp_dir: str = "./data/tmp/videos"
    whisper_model: str = "deepdml/faster-whisper-large-v3-turbo-ct2"
    whisper_endpoints: str = ""
    whisper_server_timeout: float = 300.0
    modal_app_name: str = ""
    proxy_url: str = ""
    browserless_url: str = ""
    # Bronze storage backend
    bronze_backend: str = "filesystem"  # "filesystem" or "s3"
    bronze_root: str = "./data/bronze"
    bronze_s3_endpoint: str = ""
    bronze_s3_bucket: str = "bronze"
    bronze_s3_access_key: str = ""
    bronze_s3_secret_key: str = ""
    bronze_s3_region: str = "garage"
    # Rate limits (operational, stay as env vars)
    reddit_rate_limit: float = 3.0
    hn_rate_limit: float = 1.0
    lobsters_rate_limit: float = 2.0
    telegram_rate_limit: float = 2.0
    # Telegram credentials (secrets, must be env vars)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session: str = ""  # StringSession base64 string
