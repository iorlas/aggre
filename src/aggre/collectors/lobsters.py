"""Lobsters collector using the native JSON API."""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa
import structlog

from aggre.collectors.base import BaseCollector
from aggre.config import AppConfig
from aggre.db import SilverDiscussion
from aggre.statuses import CommentsStatus
from aggre.urls import ensure_content

LOBSTERS_BASE = "https://lobste.rs"
USER_AGENT = "aggre/0.1.0 (content-aggregator)"

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "url", "meta", "score", "comment_count")


class LobstersCollector(BaseCollector):
    """Collect stories and comments from Lobsters via the JSON API."""

    source_type = "lobsters"

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        if not config.lobsters:
            return 0

        total_new = 0
        rate_limit = config.settings.lobsters_rate_limit
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)

        try:
            for lob_source in config.lobsters:
                log.info("lobsters.collecting", name=lob_source.name)
                source_id = self._ensure_source(engine, lob_source.name)

                urls: list[str] = []
                if lob_source.tags:
                    for tag in lob_source.tags:
                        urls.append(f"{LOBSTERS_BASE}/t/{tag}.json")
                else:
                    urls.append(f"{LOBSTERS_BASE}/hottest.json")
                    urls.append(f"{LOBSTERS_BASE}/newest.json")

                stories_by_id: dict[str, dict] = {}
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

                with engine.begin() as conn:
                    for short_id, story in stories_by_id.items():
                        raw_id = self._store_raw_item(conn, short_id, story)
                        discussion_id = self._store_discussion(conn, source_id, raw_id, short_id, story)
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
        config: AppConfig,
        log: structlog.stdlib.BoundLogger,
        batch_limit: int = 10,
    ) -> int:
        if batch_limit <= 0:
            return 0

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverDiscussion.id, SilverDiscussion.external_id, SilverDiscussion.meta)
                .where(
                    SilverDiscussion.source_type == "lobsters",
                    SilverDiscussion.comments_status == CommentsStatus.PENDING,
                )
                .limit(batch_limit)
            ).fetchall()

        if not rows:
            log.info("lobsters.no_pending_comments")
            return 0

        log.info("lobsters.fetching_comments", pending=len(rows))
        rate_limit = config.settings.lobsters_rate_limit
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
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

                with engine.begin() as conn:
                    comments = data.get("comments", [])
                    comments_json = json.dumps(comments)
                    comment_count = len(comments)

                    conn.execute(
                        sa.update(SilverDiscussion)
                        .where(SilverDiscussion.id == discussion_id)
                        .values(
                            comments_status=CommentsStatus.DONE,
                            comments_json=comments_json,
                            comment_count=comment_count,
                        )
                    )
                    fetched += 1

            log.info("lobsters.comments_fetched", fetched=fetched, total_pending=len(rows))
        finally:
            client.close()

        return fetched

    def search_by_url(
        self, url: str, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger,
    ) -> int:
        rate_limit = config.settings.lobsters_rate_limit
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
        new_count = 0

        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            if not domain:
                return 0

            search_url = f"{LOBSTERS_BASE}/domains/{domain}.json"
            time.sleep(rate_limit)

            resp = client.get(search_url)
            if resp.status_code == 404:
                return 0
            resp.raise_for_status()
            stories = resp.json()

            source_id = self._ensure_source(engine, "Lobsters")

            with engine.begin() as conn:
                for story in stories:
                    story_url = story.get("url", "")
                    if story_url != url:
                        continue

                    short_id = story.get("short_id")
                    if not short_id:
                        continue

                    raw_id = self._store_raw_item(conn, short_id, story)
                    discussion_id = self._store_discussion(conn, source_id, raw_id, short_id, story)
                    if discussion_id is not None:
                        new_count += 1
        finally:
            client.close()

        return new_count

    def _store_discussion(
        self, conn: sa.Connection, source_id: int, raw_id: int | None, short_id: str, story: dict,
    ) -> int | None:
        story_url = story.get("url") or story.get("comments_url", "")
        comments_url = story.get("comments_url", "")

        content_id = None
        if story.get("url") and story.get("url") != comments_url:
            content_id = ensure_content(conn, story["url"])

        meta = json.dumps({
            "tags": story.get("tags", []),
            "lobsters_url": comments_url,
        })

        values = dict(
            source_id=source_id,
            bronze_discussion_id=raw_id,
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
