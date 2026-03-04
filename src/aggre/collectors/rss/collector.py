"""RSS/Atom feed collector using feedparser."""

from __future__ import annotations

import json
import logging

import feedparser
import sqlalchemy as sa

from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.collectors.rss.config import RssConfig
from aggre.settings import Settings
from aggre.urls import ensure_content
from aggre.utils.bronze import url_hash

logger = logging.getLogger(__name__)

# Columns to update on re-insert (titles/content always fresh)
_UPSERT_COLS = ("title", "author", "url", "content_text", "meta")


class RssCollector(BaseCollector):
    """Fetches RSS/Atom feeds and stores entries in the database."""

    source_type = "rss"

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: RssConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        """Fetch RSS/Atom feeds, write bronze, return references."""
        refs: list[DiscussionRef] = []

        for rss_source in config.sources:
            logger.info("rss.collecting name=%s url=%s", rss_source.name, rss_source.url)

            source_id = self._ensure_source(engine, rss_source.name, {"url": rss_source.url})

            feed = feedparser.parse(rss_source.url)

            if feed.bozo:
                logger.warning("rss_bozo_error name=%s error=%s", rss_source.name, str(feed.bozo_exception))

            if not feed.entries:
                logger.warning("rss_no_entries name=%s", rss_source.name)
                self._update_last_fetched(engine, source_id)
                continue

            for entry in feed.entries:
                external_id = entry.get("id") or entry.get("link")
                if not external_id:
                    logger.warning("skipping_entry_no_id feed=%s", rss_source.name)
                    continue

                raw_data = dict(entry)
                # Attach feed-level metadata so process_discussion can use it
                raw_data["_feed_title"] = feed.feed.get("title", rss_source.name)

                self._write_bronze(url_hash(external_id), raw_data)
                refs.append(
                    DiscussionRef(
                        external_id=external_id,
                        raw_data=raw_data,
                        source_id=source_id,
                    )
                )

            self._update_last_fetched(engine, source_id)
            logger.info("rss.discussions_collected name=%s count=%d", rss_source.name, len(feed.entries))

        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        """Normalize one RSS entry into silver rows."""
        external_id = ref_data.get("id") or ref_data.get("link")
        if not external_id:
            return

        # Extract content fields
        content_text = ref_data.get("summary") or ""
        if not content_text:
            content_list = ref_data.get("content", [{}])
            if content_list:
                content_text = content_list[0].get("value", "")

        published_at = ref_data.get("published") or ref_data.get("updated")

        feed_title = ref_data.get("_feed_title", "")
        meta = json.dumps({"feed_title": feed_title})

        # Create content for the entry link
        link = ref_data.get("link")
        content_id = ensure_content(conn, link) if link else None

        values = dict(
            source_id=source_id,
            source_type="rss",
            external_id=external_id,
            title=ref_data.get("title"),
            author=ref_data.get("author"),
            url=link,
            content_text=content_text,
            published_at=published_at,
            meta=meta,
            content_id=content_id,
        )
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
