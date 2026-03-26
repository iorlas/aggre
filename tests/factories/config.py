from __future__ import annotations

from aggre.collectors.github_trending.config import GithubTrendingConfig
from aggre.collectors.hackernews.config import HackernewsConfig
from aggre.collectors.huggingface.config import HuggingfaceConfig
from aggre.collectors.lobsters.config import LobstersConfig
from aggre.collectors.reddit.config import RedditConfig
from aggre.collectors.rss.config import RssConfig
from aggre.collectors.telegram.config import TelegramConfig
from aggre.collectors.youtube.config import YoutubeConfig
from aggre.config import AppConfig
from aggre.settings import Settings

__all__ = ["make_config"]


def make_config(
    *,
    hackernews: HackernewsConfig | None = None,
    reddit: RedditConfig | None = None,
    rss: RssConfig | None = None,
    youtube: YoutubeConfig | None = None,
    lobsters: LobstersConfig | None = None,
    huggingface: HuggingfaceConfig | None = None,
    telegram: TelegramConfig | None = None,
    github_trending: GithubTrendingConfig | None = None,
    rate_limit: float = 0.0,
    proxy_url: str = "",
    proxy_api_url: str = "",
    browserless_url: str = "",
    whisper_endpoints: str = "http://test-whisper:8090:1:whisper-cpp:test-whisper:1",
    modal_app_name: str = "",
    telegram_api_id: int = 0,
    telegram_api_hash: str = "",
    telegram_session: str = "",
) -> AppConfig:
    """Build an AppConfig with defaults suitable for tests."""
    return AppConfig(
        hackernews=hackernews or HackernewsConfig(),
        reddit=reddit or RedditConfig(),
        rss=rss or RssConfig(),
        youtube=youtube or YoutubeConfig(),
        lobsters=lobsters or LobstersConfig(),
        huggingface=huggingface or HuggingfaceConfig(),
        telegram=telegram or TelegramConfig(),
        github_trending=github_trending or GithubTrendingConfig(),
        settings=Settings(
            hn_rate_limit=rate_limit,
            reddit_rate_limit=rate_limit,
            lobsters_rate_limit=rate_limit,
            telegram_rate_limit=rate_limit,
            proxy_url=proxy_url,
            proxy_api_url=proxy_api_url,
            browserless_url=browserless_url,
            whisper_endpoints=whisper_endpoints,
            modal_app_name=modal_app_name,
            telegram_api_id=telegram_api_id,
            telegram_api_hash=telegram_api_hash,
            telegram_session=telegram_session,
        ),
    )
