"""YAML config loading with env var overrides via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource  # noqa: F401
from aggre.collectors.huggingface.config import HuggingfaceConfig, HuggingfaceSource  # noqa: F401
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource  # noqa: F401
from aggre.collectors.reddit.config import RedditConfig, RedditSource  # noqa: F401
from aggre.collectors.rss.config import RssConfig, RssSource  # noqa: F401
from aggre.collectors.telegram.config import TelegramConfig, TelegramSource  # noqa: F401
from aggre.collectors.youtube.config import YoutubeConfig, YoutubeSource  # noqa: F401
from aggre.settings import Settings


class AppConfig(BaseModel):
    youtube: YoutubeConfig = YoutubeConfig()
    reddit: RedditConfig = RedditConfig()
    hackernews: HackernewsConfig = HackernewsConfig()
    lobsters: LobstersConfig = LobstersConfig()
    rss: RssConfig = RssConfig()
    huggingface: HuggingfaceConfig = HuggingfaceConfig()
    telegram: TelegramConfig = TelegramConfig()
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
