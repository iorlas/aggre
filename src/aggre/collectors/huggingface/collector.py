"""HuggingFace Daily Papers collector using the undocumented JSON API."""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa

from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.collectors.huggingface.config import HuggingfaceConfig
from aggre.settings import Settings
from aggre.urls import ensure_content
from aggre.utils.http import create_http_client

logger = logging.getLogger(__name__)

HF_API_URL = "https://huggingface.co/api/daily_papers"

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "content_text", "meta", "score", "comment_count")


class HuggingfaceCollector(BaseCollector):
    """Collect daily papers from HuggingFace."""

    source_type = "huggingface"

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: HuggingfaceConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        """Fetch HuggingFace daily papers, write bronze, return references."""
        if not config.sources:
            return []

        refs: list[DiscussionRef] = []

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for hf_source in config.sources:
                logger.info("huggingface.collecting name=%s", hf_source.name)
                source_id = self._ensure_source(engine, hf_source.name)

                try:
                    resp = client.get(HF_API_URL, params={"limit": config.fetch_limit})
                    resp.raise_for_status()
                    papers = resp.json()
                except Exception:
                    logger.exception("huggingface.fetch_failed")
                    continue

                for item in papers:
                    paper = item.get("paper", {})
                    paper_id = paper.get("id")
                    if not paper_id:
                        continue

                    self._write_bronze(paper_id, item)
                    refs.append(
                        DiscussionRef(
                            external_id=paper_id,
                            raw_data=item,
                            source_id=source_id,
                        )
                    )

                logger.info("huggingface.discussions_collected count=%d total_seen=%d", len(refs), len(papers))
                self._update_last_fetched(engine, source_id)

        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        """Normalize one HuggingFace paper into silver rows."""
        paper = ref_data.get("paper", {})
        paper_id = paper.get("id")
        if not paper_id:
            return

        authors = paper.get("authors", [])
        author_names = ", ".join(a.get("name", "") for a in authors if isinstance(a, dict)) if authors else None

        hf_url = f"https://huggingface.co/papers/{paper_id}"

        # Create content entry (summary stored in content_text, not SilverContent.text)
        content_id = ensure_content(conn, hf_url)
        summary = paper.get("summary")

        meta = json.dumps(
            {
                "github_repo": paper.get("githubRepo"),
            }
        )

        values = dict(
            source_id=source_id,
            source_type="huggingface",
            external_id=paper_id,
            title=paper.get("title"),
            content_text=summary,
            author=author_names,
            url=hf_url,
            published_at=paper.get("publishedAt"),
            meta=meta,
            content_id=content_id,
            score=paper.get("upvotes", 0),
            comment_count=ref_data.get("numComments", 0),
        )
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
