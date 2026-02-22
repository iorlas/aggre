"""Lobsters collector using the native JSON API."""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import sqlalchemy as sa
import structlog

from aggre.collectors.base import BaseCollector
from aggre.collectors.lobsters.config import LobstersConfig
from aggre.settings import Settings
from aggre.statuses import CommentsStatus
from aggre.urls import ensure_content
from aggre.utils.http import create_http_client

LOBSTERS_BASE = "https://lobste.rs"

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "url", "meta", "score", "comment_count")


class LobstersCollector(BaseCollector):
    """Collect stories and comments from Lobsters via the JSON API."""

    source_type = "lobsters"

    def __init__(self) -> None:
        self._domain_cache: dict[str, list[dict[str, object]]] = {}

    def collect(self, engine: sa.engine.Engine, config: LobstersConfig, settings: Settings, log: structlog.stdlib.BoundLogger) -> int:
        if not config.sources:
            return 0

        total_new = 0
        rate_limit = settings.lobsters_rate_limit
        client = create_http_client(proxy_url=settings.proxy_url or None)

        try:
            for lob_source in config.sources:
                log.info("lobsters.collecting", name=lob_source.name)
                source_id = self._ensure_source(engine, lob_source.name)

                urls: list[str] = []
                if lob_source.tags:
                    for tag in lob_source.tags:
                        urls.append(f"{LOBSTERS_BASE}/t/{tag}.json")
                else:
                    urls.append(f"{LOBSTERS_BASE}/hottest.json")
                    urls.append(f"{LOBSTERS_BASE}/newest.json")

                stories_by_id: dict[str, dict[str, object]] = {}
                for url in urls:
                    time.sleep(rate_limit)
                    try:
                        resp = client.get(url)
                        resp.raise_for_status()
                        stories = resp.json()
                    except Exception:
                        log.exception("lobsters.fetch_failed", url=url)
                        continue

                    for story in stories:
                        short_id = story.get("short_id")
                        if short_id and short_id not in stories_by_id:
                            stories_by_id[short_id] = story

                for short_id, story in stories_by_id.items():
                    self._write_bronze(short_id, story)

                with engine.begin() as conn:
                    for short_id, story in stories_by_id.items():
                        discussion_id = self._store_discussion(conn, source_id, short_id, story)
                        if discussion_id is not None:
                            total_new += 1

                log.info("lobsters.discussions_stored", new=total_new, total_seen=len(stories_by_id))
                self._update_last_fetched(engine, source_id)
        finally:
            client.close()

        return total_new

    def collect_comments(
        self,
        engine: sa.engine.Engine,
        config: LobstersConfig,
        settings: Settings,
        log: structlog.stdlib.BoundLogger,
        batch_limit: int = 10,
    ) -> int:
        if batch_limit <= 0:
            return 0

        rows = self._query_pending_comments(engine, batch_limit)

        if not rows:
            log.info("lobsters.no_pending_comments")
            return 0

        log.info("lobsters.fetching_comments", pending=len(rows))
        rate_limit = settings.lobsters_rate_limit
        client = create_http_client(proxy_url=settings.proxy_url or None)
        fetched = 0

        try:
            for row in rows:
                discussion_id = row.id
                short_id = row.external_id

                url = f"{LOBSTERS_BASE}/s/{short_id}.json"
                time.sleep(rate_limit)

                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception:
                    log.exception("lobsters.comments_fetch_failed", story_id=short_id)
                    continue

                comments = data.get("comments", [])
                self._mark_comments_done(engine, discussion_id, json.dumps(comments), len(comments))
                fetched += 1

            log.info("lobsters.comments_fetched", fetched=fetched, total_pending=len(rows))
        finally:
            client.close()

        return fetched

    def search_by_url(
        self,
        url: str,
        engine: sa.engine.Engine,
        config: LobstersConfig,
        settings: Settings,
        log: structlog.stdlib.BoundLogger,
    ) -> int:
        parsed = urlparse(url)
        domain = parsed.netloc
        if not domain:
            return 0

        # Use cached domain stories, or fetch and cache
        if domain not in self._domain_cache:
            rate_limit = settings.lobsters_rate_limit
            client = create_http_client(proxy_url=settings.proxy_url or None)
            try:
                search_url = f"{LOBSTERS_BASE}/domains/{domain}.json"
                time.sleep(rate_limit)

                resp = client.get(search_url)
                if resp.status_code in (404, 429):
                    self._domain_cache[domain] = []
                    if resp.status_code == 429:
                        log.warning("lobsters.rate_limited", domain=domain)
                    return 0
                resp.raise_for_status()
                self._domain_cache[domain] = resp.json()
            except Exception:
                self._domain_cache[domain] = []
                raise
            finally:
                client.close()

        stories = self._domain_cache[domain]
        if not stories:
            return 0

        new_count = 0
        source_id = self._ensure_source(engine, "Lobsters")

        with engine.begin() as conn:
            for story in stories:
                story_url = story.get("url", "")
                if story_url != url:
                    continue

                short_id = story.get("short_id")
                if not short_id:
                    continue

                self._write_bronze(short_id, story)
                discussion_id = self._store_discussion(conn, source_id, short_id, story)
                if discussion_id is not None:
                    new_count += 1

        return new_count

    def _store_discussion(
        self,
        conn: sa.Connection,
        source_id: int,
        short_id: str,
        story: dict[str, object],
    ) -> int | None:
        story_url = story.get("url") or story.get("comments_url", "")
        comments_url = story.get("comments_url", "")

        content_id = None
        if story.get("url") and story.get("url") != comments_url:
            content_id = ensure_content(conn, story["url"])

        meta = json.dumps(
            {
                "tags": story.get("tags", []),
                "lobsters_url": comments_url,
            }
        )

        values = dict(
            source_id=source_id,
            source_type="lobsters",
            external_id=short_id,
            title=story.get("title"),
            author=(
                story.get("submitter_user", {}).get("username")
                if isinstance(story.get("submitter_user"), dict)
                else story.get("submitter_user")
            ),
            url=story_url,
            published_at=story.get("created_at"),
            meta=meta,
            content_id=content_id,
            comments_status=CommentsStatus.PENDING,
            score=story.get("score", 0),
            comment_count=story.get("comment_count", 0),
        )
        return self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
