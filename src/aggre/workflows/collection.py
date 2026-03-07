"""Collection workflows — one per source, each on a cron schedule."""

from __future__ import annotations

import logging

import sqlalchemy as sa

from aggre.collectors.arxiv.collector import ArxivCollector
from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.huggingface.collector import HuggingfaceCollector
from aggre.collectors.lesswrong.collector import LesswrongCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.reddit.collector import RedditCollector
from aggre.collectors.rss.collector import RssCollector
from aggre.collectors.telegram.collector import TelegramCollector
from aggre.collectors.youtube.collector import YoutubeCollector
from aggre.config import load_config
from aggre.utils.db import get_engine

logger = logging.getLogger(__name__)


def collect_source(engine: sa.engine.Engine, cfg: object, name: str, collector_cls: type) -> int:
    """Collect discussions for one source, process into silver. Returns count."""
    source_config = getattr(cfg, name)
    collector = collector_cls()
    refs = collector.collect_discussions(engine, source_config, cfg.settings)
    logger.info("collect.fetched source=%s discussions=%d", name, len(refs))
    count = 0
    errors = 0
    for ref in refs:
        try:
            with engine.begin() as conn:
                collector.process_discussion(ref["raw_data"], conn, ref["source_id"])
            count += 1
        except Exception:
            logger.exception("collect.process_error source=%s external_id=%s", name, ref["external_id"])
            errors += 1
    logger.info("collect.source_complete source=%s fetched=%d processed=%d errors=%d", name, len(refs), count, errors)
    return count


# -- Source configs: (name, collector_class, cron_schedule) --

_SOURCES = [
    ("hackernews", HackernewsCollector, "0 * * * *"),
    ("reddit", RedditCollector, "0 * * * *"),
    ("lobsters", LobstersCollector, "0 * * * *"),
    ("rss", RssCollector, "0 */2 * * *"),
    ("huggingface", HuggingfaceCollector, "0 */3 * * *"),
    ("telegram", TelegramCollector, "0 */3 * * *"),
    ("youtube", YoutubeCollector, "0 */6 * * *"),
    ("arxiv", ArxivCollector, "0 */6 * * *"),
    ("lesswrong", LesswrongCollector, "0 */3 * * *"),
]


def register(h) -> list:  # pragma: no cover — Hatchet wiring
    """Register all collection workflows with the Hatchet instance."""
    workflows = []
    for source_name, collector_cls, cron in _SOURCES:
        wf = h.workflow(name=f"collect-{source_name}", on_crons=[cron])

        # Capture loop variables in closure
        _name = source_name
        _cls = collector_cls

        @wf.task(execution_timeout="30m")
        def collect(input, ctx, _name=_name, _cls=_cls):  # noqa: A002
            ctx.log(f"Collecting {_name}")
            cfg = load_config()
            engine = get_engine(cfg.settings.database_url)
            count = collect_source(engine, cfg, _name, _cls)
            ctx.log(f"Collected {count} discussions from {_name}")
            if count > 0:
                h.event.push("content.new", {"source": _name, "count": count})
            return {"source": _name, "count": count}

        workflows.append(wf)
    return workflows
