"""RSS/Atom feed collector using feedparser."""

from __future__ import annotations

import json

import feedparser
import sqlalchemy as sa
import structlog

from aggre.collectors.base import BaseCollector
from aggre.config import AppConfig
from aggre.db import SilverContent
from aggre.urls import ensure_content

# Columns to update on re-insert (titles/content always fresh)
_UPSERT_COLS = ("title", "author", "url", "content_text", "meta")


class RssCollector(BaseCollector):
    """Fetches RSS/Atom feeds and stores entries in the database."""

    source_type = "rss"

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        total_new = 0

        for rss_source in config.rss:
            log.info("rss.collecting", name=rss_source.name, url=rss_source.url)

            source_id = self._ensure_source(engine, rss_source.name, {"url": rss_source.url})

            feed = feedparser.parse(rss_source.url)

            if feed.bozo:
                log.warning("rss_bozo_error", name=rss_source.name, error=str(feed.bozo_exception))

            if not feed.entries:
                log.warning("rss_no_entries", name=rss_source.name)
                continue

            new_count = 0

            for entry in feed.entries:
                external_id = entry.get("id") or entry.get("link")
                if not external_id:
                    log.warning("skipping_entry_no_id", feed=rss_source.name)
                    continue

                raw_data = json.dumps(dict(entry))

                with engine.begin() as conn:
                    raw_id = self._store_raw_item(conn, external_id, raw_data)

                    # Extract content fields
                    content_text = entry.get("summary") or ""
                    if not content_text:
                        content_list = entry.get("content", [{}])
                        if content_list:
                            content_text = content_list[0].get("value", "")

                    published_at = entry.get("published") or entry.get("updated")

                    meta = json.dumps({"feed_title": feed.feed.get("title", rss_source.name)})

                    # Create content for the entry link
                    content_id = ensure_content(conn, entry.get("link")) if entry.get("link") else None
                    if content_id and content_text:
                        conn.execute(
                            sa.update(SilverContent)
                            .where(SilverContent.id == content_id, SilverContent.body_text.is_(None))
                            .values(body_text=content_text)
                        )

                    values = dict(
                        source_id=source_id,
                        bronze_discussion_id=raw_id,
                        source_type="rss",
                        external_id=external_id,
                        title=entry.get("title"),
                        author=entry.get("author"),
                        url=entry.get("link"),
                        content_text=content_text,
                        published_at=published_at,
                        meta=meta,
                        content_id=content_id,
                    )
                    result = self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
                    if result is not None:
                        new_count += 1

            self._update_last_fetched(engine, source_id)
            log.info("rss.discussions_stored", name=rss_source.name, new_discussions=new_count)
            total_new += new_count

        return total_new
