"""Lobsters collector using the native JSON API."""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlparse

import sqlalchemy as sa

from aggre.collectors.base import BaseCollector, ContentReference
from aggre.collectors.lobsters.config import LobstersConfig
from aggre.settings import Settings
from aggre.urls import ensure_content
from aggre.utils.bronze import write_bronze
from aggre.utils.http import create_http_client

logger = logging.getLogger(__name__)

LOBSTERS_BASE = "https://lobste.rs"

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "url", "meta", "score", "comment_count")


class LobstersCollector(BaseCollector):
    """Collect stories and comments from Lobsters via the JSON API."""

    source_type = "lobsters"

    def __init__(self) -> None:
        self._domain_cache: dict[str, list[dict[str, object]]] = {}

    def collect_references(
        self,
        engine: sa.engine.Engine,
        config: LobstersConfig,
        settings: Settings,
    ) -> list[ContentReference]:
        if not config.sources:
            return []

        refs: list[ContentReference] = []
        rate_limit = settings.lobsters_rate_limit

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for lob_source in config.sources:
                logger.info("lobsters.collecting name=%s", lob_source.name)
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
                        logger.exception("lobsters.fetch_failed url=%s", url)
                        continue

                    for story in stories:
                        short_id = story.get("short_id")
                        if short_id and short_id not in stories_by_id:
                            stories_by_id[short_id] = story

                for short_id, story in stories_by_id.items():
                    self._write_bronze(short_id, story)
                    refs.append(ContentReference(external_id=short_id, raw_data=story, source_id=source_id))

                logger.info("lobsters.references_collected count=%d", len(stories_by_id))
                self._update_last_fetched(engine, source_id)

        return refs

    def process_reference(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        story = ref_data
        short_id = story.get("short_id", "")

        story_url = story.get("url") or story.get("comments_url", "")
        comments_url = story.get("comments_url", "")

        content_id = None
        if story.get("url") and story.get("url") != comments_url:
            # Link post — ensure content for the external URL
            content_id = ensure_content(conn, story["url"])
        elif not story.get("url") or story.get("url") == comments_url:
            # Self-post — create content with the description text
            content_id = self._ensure_self_post_content(conn, comments_url, story.get("description", ""))

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
            score=story.get("score", 0),
            comment_count=story.get("comment_count", 0),
        )
        self._upsert_observation(conn, values, update_columns=_UPSERT_COLS)

    def collect_comments(
        self,
        engine: sa.engine.Engine,
        config: LobstersConfig,
        settings: Settings,
        batch_limit: int = 10,
    ) -> int:
        if batch_limit <= 0:
            return 0

        rows = self._query_pending_comments(engine, batch_limit)

        if not rows:
            logger.info("lobsters.no_pending_comments")
            return 0

        logger.info("lobsters.fetching_comments pending=%d", len(rows))
        rate_limit = settings.lobsters_rate_limit
        fetched = 0

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
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
                    logger.exception("lobsters.comments_fetch_failed story_id=%s", short_id)
                    continue

                # Write raw API response to bronze before storing in silver
                write_bronze(self.source_type, short_id, "comments", json.dumps(data, ensure_ascii=False), "json")

                comments = data.get("comments", [])
                self._mark_comments_done(engine, discussion_id, json.dumps(comments), len(comments))
                fetched += 1

            logger.info("lobsters.comments_fetched fetched=%d total_pending=%d", fetched, len(rows))

        return fetched

    def search_by_url(
        self,
        url: str,
        engine: sa.engine.Engine,
        config: LobstersConfig,
        settings: Settings,
    ) -> int:
        parsed = urlparse(url)
        domain = parsed.netloc
        if not domain:
            return 0

        # Use cached domain stories, or fetch and cache
        if domain not in self._domain_cache:
            rate_limit = settings.lobsters_rate_limit
            try:
                with create_http_client(proxy_url=settings.proxy_url or None) as client:
                    search_url = f"{LOBSTERS_BASE}/domains/{domain}.json"
                    time.sleep(rate_limit)

                    resp = client.get(search_url)
                    if resp.status_code in (404, 429):
                        self._domain_cache[domain] = []
                        if resp.status_code == 429:
                            logger.warning("lobsters.rate_limited domain=%s", domain)
                        return 0
                    resp.raise_for_status()
                    self._domain_cache[domain] = resp.json()
            except Exception:
                self._domain_cache[domain] = []
                raise

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
                self.process_reference(story, conn, source_id)
                new_count += 1

        return new_count
