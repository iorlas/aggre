"""Collector registry."""

from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.huggingface.collector import HuggingfaceCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.reddit.collector import RedditCollector
from aggre.collectors.rss.collector import RssCollector
from aggre.collectors.telegram.collector import TelegramCollector
from aggre.collectors.youtube.collector import YoutubeCollector

COLLECTORS = {
    "youtube": YoutubeCollector,
    "reddit": RedditCollector,
    "hackernews": HackernewsCollector,
    "lobsters": LobstersCollector,
    "rss": RssCollector,
    "huggingface": HuggingfaceCollector,
    "telegram": TelegramCollector,
}
