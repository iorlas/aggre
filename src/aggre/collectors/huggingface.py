"""HuggingFace Daily Papers collector using the undocumented JSON API."""

from __future__ import annotations

import json

import httpx
import sqlalchemy as sa
import structlog

from aggre.collectors.base import BaseCollector
from aggre.config import AppConfig
from aggre.db import SilverContent
from aggre.http import create_http_client
from aggre.urls import ensure_content

HF_API_URL = "https://huggingface.co/api/daily_papers"

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "content_text", "meta", "score", "comment_count")


class HuggingfaceCollector(BaseCollector):
    """Collect daily papers from HuggingFace."""

    source_type = "huggingface"

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        if not config.huggingface:
            return 0

        total_new = 0
        client = create_http_client(proxy_url=config.settings.proxy_url or None)

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
                        discussion_id = self._store_discussion(conn, source_id, raw_id, paper_id, item)
                        if discussion_id is not None:
                            total_new += 1

                log.info("huggingface.discussions_stored", new=total_new, total_seen=len(papers))
                self._update_last_fetched(engine, source_id)
        finally:
            client.close()

        return total_new

    def _store_discussion(
        self, conn: sa.Connection, source_id: int, raw_id: int | None, paper_id: str, item: dict,
    ) -> int | None:
        paper = item.get("paper", {})

        authors = paper.get("authors", [])
        author_names = ", ".join(
            a.get("name", "") for a in authors if isinstance(a, dict)
        ) if authors else None

        hf_url = f"https://huggingface.co/papers/{paper_id}"

        # Create content entry and set summary as body_text
        content_id = ensure_content(conn, hf_url)
        summary = paper.get("summary")
        if content_id and summary:
            conn.execute(
                sa.update(SilverContent)
                .where(SilverContent.id == content_id, SilverContent.body_text.is_(None))
                .values(body_text=summary)
            )

        meta = json.dumps({
            "github_repo": item.get("paper", {}).get("githubRepo"),
        })

        values = dict(
            source_id=source_id,
            bronze_discussion_id=raw_id,
            source_type="huggingface",
            external_id=paper_id,
            title=paper.get("title"),
            content_text=summary,
            author=author_names,
            url=hf_url,
            published_at=paper.get("publishedAt"),
            meta=meta,
            content_id=content_id,
            score=item.get("paper", {}).get("upvotes", 0),
            comment_count=item.get("numComments", 0),
        )
        return self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
