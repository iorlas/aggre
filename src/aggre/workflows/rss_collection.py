"""RSS collection workflow — fan-out per feed via Hatchet child workflows."""

from __future__ import annotations

import logging

from aggre.collectors.rss.collector import RssCollector
from aggre.collectors.rss.config import RssConfig, RssSource
from aggre.config import load_config
from aggre.utils.db import get_engine
from aggre.workflows.collection import collect_source
from aggre.workflows.models import CollectResult, RssSourceInput

logger = logging.getLogger(__name__)


def register(h):  # pragma: no cover — Hatchet wiring
    child_wf = h.workflow(name="collect-rss-feed", input_validator=RssSourceInput)

    @child_wf.task(execution_timeout="5m", schedule_timeout="720h")
    def rss_collect_one(input: RssSourceInput, ctx):
        cfg = load_config()
        engine = get_engine(cfg.settings.database_url)
        single_config = RssConfig(sources=[RssSource(name=input.name, url=input.url)])
        count = collect_source(engine, cfg, "rss", RssCollector, source_config=single_config, hatchet=h)
        ctx.log(f"Collected {count} from {input.name}")
        return CollectResult(source=f"rss:{input.name}", succeeded=count, total=count)

    parent_wf = h.workflow(name="collect-rss", on_crons=["0 */2 * * *"])

    @parent_wf.task(execution_timeout="10m", schedule_timeout="720h")
    async def rss_fan_out(input, ctx):
        cfg = load_config()
        ctx.log(f"Fanning out to {len(cfg.rss.sources)} RSS feeds")
        results = await child_wf.aio_run_many(
            [
                child_wf.create_bulk_run_item(
                    input=RssSourceInput(name=src.name, url=src.url),
                )
                for src in cfg.rss.sources
            ]
        )
        total = sum(r.get("total", 0) for r in results)
        ctx.log(f"RSS complete: {total} total from {len(results)} feeds")
        return CollectResult(source="rss", succeeded=total, total=total)

    return [parent_wf, child_wf]
