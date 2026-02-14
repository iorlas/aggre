"""HuggingFace Daily Papers collector using the undocumented JSON API."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
import structlog

from aggre.config import AppConfig
from aggre.db import BronzePost, SilverPost, Source

HF_API_URL = "https://huggingface.co/api/daily_papers"
USER_AGENT = "aggre/0.1.0 (content-aggregator)"


class HuggingfaceCollector:
    """Collect daily papers from HuggingFace."""

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        if not config.huggingface:
            return 0

        total_new = 0
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)

        try:
            for hf_source in config.huggingface:
                log.info("huggingface.collecting", name=hf_source.name)
                source_id = self._ensure_source(engine, hf_source.name)

                try:
                    resp = client.get(HF_API_URL, params={"limit": 100})
                    resp.raise_for_status()
                    papers = resp.json()
                except Exception:
                    log.exception("huggingface.fetch_failed")
                    continue

                with engine.begin() as conn:
                    for item in papers:
                        paper = item.get("paper", {})
                        paper_id = paper.get("id")
                        if not paper_id:
                            continue

                        raw_id = self._store_raw_item(conn, paper_id, item)
                        if raw_id is not None:
                            ci_id = self._store_content_item(conn, source_id, raw_id, paper_id, item)
                            if ci_id is not None:
                                total_new += 1

                log.info("huggingface.papers_stored", new=total_new, total_seen=len(papers))

                with engine.begin() as conn:
                    conn.execute(
                        sa.update(Source).where(Source.id == source_id)
                        .values(last_fetched_at=datetime.now(UTC).isoformat())
                    )
        finally:
            client.close()

        return total_new

    def _ensure_source(self, engine: sa.engine.Engine, name: str) -> int:
        with engine.begin() as conn:
            row = conn.execute(
                sa.select(Source.id).where(Source.type == "huggingface", Source.name == name)
            ).first()
            if row:
                return row[0]
            result = conn.execute(
                sa.insert(Source).values(
                    type="huggingface",
                    name=name,
                    config=json.dumps({"name": name}),
                )
            )
            return result.lastrowid

    def _store_raw_item(self, conn: sa.Connection, paper_id: str, item: dict) -> int | None:
        result = conn.execute(
            sa.insert(BronzePost)
            .prefix_with("OR IGNORE")
            .values(
                source_type="huggingface",
                external_id=paper_id,
                raw_data=json.dumps(item),
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid

    def _store_content_item(
        self, conn: sa.Connection, source_id: int, raw_id: int, paper_id: str, item: dict,
    ) -> int | None:
        paper = item.get("paper", {})

        authors = paper.get("authors", [])
        author_names = ", ".join(
            a.get("name", "") for a in authors if isinstance(a, dict)
        ) if authors else None

        meta = json.dumps({
            "upvotes": item.get("paper", {}).get("upvotes", 0),
            "num_comments": item.get("numComments", 0),
            "github_repo": item.get("paper", {}).get("githubRepo"),
        })

        result = conn.execute(
            sa.insert(SilverPost)
            .prefix_with("OR IGNORE")
            .values(
                source_id=source_id,
                bronze_post_id=raw_id,
                source_type="huggingface",
                external_id=paper_id,
                title=paper.get("title"),
                content_text=paper.get("summary"),
                author=author_names,
                url=f"https://huggingface.co/papers/{paper_id}",
                published_at=paper.get("publishedAt"),
                meta=meta,
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid
