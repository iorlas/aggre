"""ArXiv RSS feed collector using feedparser."""

from __future__ import annotations

import json
import logging
import re

import feedparser
import sqlalchemy as sa

from aggre.collectors.arxiv.config import ArxivConfig
from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.settings import Settings
from aggre.urls import ensure_content
from aggre.utils.bronze import url_hash

logger = logging.getLogger(__name__)

# ArXiv RSS feed URL template
_FEED_URL = "http://export.arxiv.org/rss/{category}"

# Regex to extract paper ID (e.g., "2602.23360" from a link like ".../abs/2602.23360v1")
_PAPER_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")

# Columns to update on re-insert (titles/content always fresh)
_UPSERT_COLS = ("title", "author", "url", "content_text", "meta")


class ArxivCollector(BaseCollector):
    """Fetches ArXiv RSS feeds and stores entries in the database."""

    source_type = "arxiv"

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: ArxivConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        """Fetch ArXiv RSS feeds, write bronze, return references."""
        refs: list[DiscussionRef] = []

        for arxiv_source in config.sources:
            url = _FEED_URL.format(category=arxiv_source.category)
            logger.info("arxiv.collecting name=%s category=%s", arxiv_source.name, arxiv_source.category)

            source_id = self._ensure_source(engine, arxiv_source.name, {"category": arxiv_source.category})

            feed = feedparser.parse(url)

            if feed.bozo:
                logger.warning("arxiv_bozo_error name=%s error=%s", arxiv_source.name, str(feed.bozo_exception))

            if not feed.entries:
                logger.warning("arxiv_no_entries name=%s", arxiv_source.name)
                self._update_last_fetched(engine, source_id)
                continue

            for entry in feed.entries:
                link = entry.get("link", "")
                m = _PAPER_ID_RE.search(link)
                if not m:
                    logger.warning("skipping_entry_no_paper_id name=%s link=%s", arxiv_source.name, link)
                    continue

                external_id = m.group(1)

                raw_data = dict(entry)
                raw_data["_arxiv_category"] = arxiv_source.category

                self._write_bronze(url_hash(external_id), raw_data)
                refs.append(
                    DiscussionRef(
                        external_id=external_id,
                        raw_data=raw_data,
                        source_id=source_id,
                    )
                )

            self._update_last_fetched(engine, source_id)
            logger.info("arxiv.discussions_collected name=%s count=%d", arxiv_source.name, len(feed.entries))

        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        """Normalize one ArXiv entry into silver rows."""
        link = ref_data.get("link", "")
        m = _PAPER_ID_RE.search(str(link))
        if not m:
            return

        external_id = m.group(1)

        # Abstract goes to content_text (NOT SilverContent.text, per null-check pattern)
        content_text = ref_data.get("summary") or ""

        published_at = ref_data.get("published")

        # Build category list from entry tags
        tags = ref_data.get("tags", [])
        categories = [tag.get("term", "") for tag in tags if isinstance(tag, dict)] if isinstance(tags, list) else []
        category = ref_data.get("_arxiv_category", "")
        if category and category not in categories:
            categories.insert(0, category)

        meta = json.dumps({"categories": categories, "arxiv_url": str(link)})

        # Create content for the paper page (webpage pipeline will fetch it)
        content_id = ensure_content(conn, str(link)) if link else None

        values = dict(
            source_id=source_id,
            source_type="arxiv",
            external_id=external_id,
            title=ref_data.get("title"),
            author=ref_data.get("author", ""),
            url=str(link),
            content_text=str(content_text),
            published_at=published_at,
            meta=meta,
            content_id=content_id,
        )
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
