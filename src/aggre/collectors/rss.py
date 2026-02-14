"""RSS/Atom feed collector using feedparser."""

from __future__ import annotations

import json

import feedparser
import sqlalchemy as sa
import structlog

from aggre.config import AppConfig
from aggre.db import content_items, raw_items, sources


class RssCollector:
    """Fetches RSS/Atom feeds and stores entries in the database."""

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        total_new = 0

        for rss_source in config.rss:
            log.info("fetching_rss", name=rss_source.name, url=rss_source.url)

            with engine.begin() as conn:
                # Ensure source row exists
                row = conn.execute(
                    sa.select(sources.c.id).where(
                        sources.c.type == "rss",
                        sources.c.name == rss_source.name,
                    )
                ).fetchone()

                if row is None:
                    result = conn.execute(
                        sa.insert(sources).values(
                            type="rss",
                            name=rss_source.name,
                            config=json.dumps({"url": rss_source.url}),
                        )
                    )
                    source_id = result.inserted_primary_key[0]
                else:
                    source_id = row[0]

            feed = feedparser.parse(rss_source.url)
            new_count = 0

            for entry in feed.entries:
                external_id = entry.get("id") or entry.get("link")
                if not external_id:
                    log.warning("skipping_entry_no_id", feed=rss_source.name)
                    continue

                raw_data = json.dumps(dict(entry))

                with engine.begin() as conn:
                    # Insert raw item (dedup by unique constraint)
                    result = conn.execute(
                        sa.insert(raw_items)
                        .prefix_with("OR IGNORE")
                        .values(
                            source_type="rss",
                            external_id=external_id,
                            raw_data=raw_data,
                        )
                    )

                    if result.rowcount == 0:
                        continue

                    raw_item_id = result.inserted_primary_key[0]

                    # Extract content fields
                    content_text = entry.get("summary") or ""
                    if not content_text:
                        content_list = entry.get("content", [{}])
                        if content_list:
                            content_text = content_list[0].get("value", "")

                    published_at = entry.get("published") or entry.get("updated")

                    meta = json.dumps({"feed_title": feed.feed.get("title", rss_source.name)})

                    conn.execute(
                        sa.insert(content_items)
                        .prefix_with("OR IGNORE")
                        .values(
                            source_id=source_id,
                            raw_item_id=raw_item_id,
                            source_type="rss",
                            external_id=external_id,
                            title=entry.get("title"),
                            author=entry.get("author"),
                            url=entry.get("link"),
                            content_text=content_text,
                            published_at=published_at,
                            metadata=meta,
                        )
                    )

                    new_count += 1

            # Update last_fetched_at
            with engine.begin() as conn:
                conn.execute(sa.update(sources).where(sources.c.id == source_id).values(last_fetched_at=sa.text("datetime('now')")))

            log.info("rss_fetch_complete", name=rss_source.name, new_items=new_count)
            total_new += new_count

        return total_new
