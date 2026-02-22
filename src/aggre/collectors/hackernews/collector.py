"""Hacker News collector using the Algolia API."""

from __future__ import annotations

import json
import time

import sqlalchemy as sa
import structlog

from aggre.collectors.base import BaseCollector
from aggre.collectors.hackernews.config import HackernewsConfig
from aggre.http import create_http_client
from aggre.settings import Settings
from aggre.statuses import CommentsStatus
from aggre.urls import ensure_content

HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "url", "meta", "score", "comment_count")


class HackernewsCollector(BaseCollector):
    """Collect stories and comments from Hacker News via the Algolia API."""

    source_type = "hackernews"

    def collect(self, engine: sa.engine.Engine, config: HackernewsConfig, settings: Settings, log: structlog.stdlib.BoundLogger) -> int:
        if not config.sources:
            return 0

        total_new = 0
        rate_limit = settings.hn_rate_limit
        client = create_http_client(proxy_url=settings.proxy_url or None)

        try:
            for hn_source in config.sources:
                log.info("hackernews.collecting", name=hn_source.name)
                source_id = self._ensure_source(engine, hn_source.name)

                url = f"{HN_ALGOLIA_BASE}/search_by_date?tags=story,front_page&hitsPerPage={config.fetch_limit}"
                time.sleep(rate_limit)

                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception:
                    log.exception("hackernews.fetch_failed")
                    continue

                hits = data.get("hits", [])
                with engine.begin() as conn:
                    for hit in hits:
                        object_id = str(hit.get("objectID", ""))
                        if not object_id:
                            continue

                        self._write_bronze(object_id, hit)
                        discussion_id = self._store_discussion(conn, source_id, object_id, hit)
                        if discussion_id is not None:
                            total_new += 1

                log.info("hackernews.discussions_stored", new=total_new, total_hits=len(hits))
                self._update_last_fetched(engine, source_id)
        finally:
            client.close()

        return total_new

    def collect_comments(
        self,
        engine: sa.engine.Engine,
        config: HackernewsConfig,
        settings: Settings,
        log: structlog.stdlib.BoundLogger,
        batch_limit: int = 10,
    ) -> int:
        if batch_limit <= 0:
            return 0

        rows = self._query_pending_comments(engine, batch_limit)

        if not rows:
            log.info("hackernews.no_pending_comments")
            return 0

        log.info("hackernews.fetching_comments", pending=len(rows))
        rate_limit = settings.hn_rate_limit
        client = create_http_client(proxy_url=settings.proxy_url or None)
        fetched = 0

        try:
            for row in rows:
                discussion_id = row.id
                ext_id = row.external_id

                url = f"{HN_ALGOLIA_BASE}/items/{ext_id}"
                time.sleep(rate_limit)

                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception:
                    log.exception("hackernews.comments_fetch_failed", story_id=ext_id)
                    continue

                children = data.get("children", [])
                self._mark_comments_done(engine, discussion_id, json.dumps(children), len(children))
                fetched += 1

            log.info("hackernews.comments_fetched", fetched=fetched, total_pending=len(rows))
        finally:
            client.close()

        return fetched

    def search_by_url(
        self,
        url: str,
        engine: sa.engine.Engine,
        config: HackernewsConfig,
        settings: Settings,
        log: structlog.stdlib.BoundLogger,
    ) -> int:
        rate_limit = settings.hn_rate_limit
        client = create_http_client(proxy_url=settings.proxy_url or None)
        new_count = 0

        try:
            search_url = f"{HN_ALGOLIA_BASE}/search?query={url}&tags=story&restrictSearchableAttributes=url"
            time.sleep(rate_limit)

            resp = client.get(search_url)
            if resp.status_code == 404:
                return 0
            resp.raise_for_status()
            data = resp.json()

            source_id = self._ensure_source(engine, "Hacker News")

            hits = data.get("hits", [])
            with engine.begin() as conn:
                for hit in hits:
                    object_id = str(hit.get("objectID", ""))
                    if not object_id:
                        continue

                    self._write_bronze(object_id, hit)
                    discussion_id = self._store_discussion(conn, source_id, object_id, hit)
                    if discussion_id is not None:
                        new_count += 1
        finally:
            client.close()

        return new_count

    def _store_discussion(
        self,
        conn: sa.Connection,
        source_id: int,
        ext_id: str,
        hit: dict,
    ) -> int | None:
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={ext_id}"
        hn_url = f"https://news.ycombinator.com/item?id={ext_id}"

        content_id = None
        if hit.get("url"):
            content_id = ensure_content(conn, hit["url"])

        created_at_str = hit.get("created_at")
        published_at = created_at_str if created_at_str else None

        meta = json.dumps({"hn_url": hn_url})

        values = dict(
            source_id=source_id,
            source_type="hackernews",
            external_id=ext_id,
            title=hit.get("title"),
            author=hit.get("author"),
            url=story_url,
            published_at=published_at,
            meta=meta,
            content_id=content_id,
            comments_status=CommentsStatus.PENDING,
            score=hit.get("points", 0),
            comment_count=hit.get("num_comments", 0),
        )
        return self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
