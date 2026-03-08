"""Collection workflows — one per source, each on a cron schedule."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from hatchet_sdk import Hatchet

from aggre.collectors.arxiv.collector import ArxivCollector
from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.huggingface.collector import HuggingfaceCollector
from aggre.collectors.lesswrong.collector import LesswrongCollector
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.reddit.collector import RedditCollector
from aggre.collectors.telegram.collector import TelegramCollector
from aggre.collectors.youtube.collector import YoutubeCollector
from aggre.config import AppConfig, load_config
from aggre.db import SilverContent, SilverDiscussion
from aggre.utils.db import get_engine
from aggre.workflows.models import CollectResult

logger = logging.getLogger(__name__)


def collect_source(
    engine: sa.engine.Engine,
    cfg: AppConfig,
    name: str,
    collector_cls: type,
    *,
    source_config: object | None = None,
    hatchet: Hatchet | None = None,
) -> int:
    """Collect discussions for one source, process into silver. Returns count.

    If hatchet is provided, emits "item.new" events for downstream processing.
    """
    if source_config is None:
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

            # Emit event for downstream processing workflows
            if hatchet is not None:
                _emit_item_event(engine, hatchet, ref, name)
        except Exception:
            logger.exception("collect.process_error source=%s external_id=%s", name, ref["external_id"])
            errors += 1
    logger.info("collect.source_complete source=%s fetched=%d processed=%d errors=%d", name, len(refs), count, errors)
    return count


def _emit_item_event(
    engine: sa.engine.Engine,
    hatchet: Hatchet,
    ref: dict,
    source_name: str,
) -> None:
    """Emit an 'item.new' event for a processed discussion."""
    try:
        with engine.connect() as conn:
            disc = conn.execute(
                sa.select(
                    SilverDiscussion.id,
                    SilverDiscussion.content_id,
                    SilverContent.domain,
                )
                .outerjoin(SilverContent, SilverContent.id == SilverDiscussion.content_id)
                .where(
                    SilverDiscussion.source_type == source_name,
                    SilverDiscussion.external_id == ref["external_id"],
                )
            ).first()

        if disc and disc.content_id:
            hatchet.event.push(
                "item.new",
                {
                    "content_id": disc.content_id,
                    "discussion_id": disc.id,
                    "source": source_name,
                    "domain": disc.domain,
                },
            )
    except Exception:
        logger.exception("collect.event_emit_error source=%s external_id=%s", source_name, ref["external_id"])


# -- Source configs: (name, collector_class, cron_schedule) --

_SOURCES = [
    ("hackernews", HackernewsCollector, "0 * * * *"),
    ("reddit", RedditCollector, "0 * * * *"),
    ("lobsters", LobstersCollector, "0 * * * *"),
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
        def collect(input, ctx, _name=_name, _cls=_cls):
            ctx.log(f"Collecting {_name}")
            cfg = load_config()
            engine = get_engine(cfg.settings.database_url)
            count = collect_source(engine, cfg, _name, _cls, hatchet=h)
            ctx.log(f"Collected {count} discussions from {_name}")
            return CollectResult(source=_name, succeeded=count, total=count)

        workflows.append(wf)
    return workflows
