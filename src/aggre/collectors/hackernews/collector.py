"""Hacker News collector using the Algolia API."""

from __future__ import annotations

import json
import logging
import time

import sqlalchemy as sa

from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.collectors.hackernews.config import HackernewsConfig
from aggre.settings import Settings
from aggre.urls import ensure_content
from aggre.utils.bronze import write_bronze
from aggre.utils.http import create_http_client

logger = logging.getLogger(__name__)

HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "url", "meta", "score", "comment_count")


class HackernewsCollector(BaseCollector):
    """Collect stories and comments from Hacker News via the Algolia API."""

    source_type = "hackernews"

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: HackernewsConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        """Fetch HN front-page stories, write bronze, return references."""
        if not config.sources:
            return []

        refs: list[DiscussionRef] = []
        rate_limit = settings.hn_rate_limit

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for hn_source in config.sources:
                logger.info("hackernews.collecting name=%s", hn_source.name)
                source_id = self._ensure_source(engine, hn_source.name)

                time.sleep(rate_limit)

                try:
                    resp = client.get(
                        f"{HN_ALGOLIA_BASE}/search_by_date",
                        params={
                            "tags": "story,front_page",
                            "hitsPerPage": config.fetch_limit,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception:
                    logger.exception("hackernews.fetch_failed")
                    continue

                hits = data.get("hits", [])
                for hit in hits:
                    object_id = str(hit.get("objectID", ""))
                    if not object_id:
                        continue

                    self._write_bronze(object_id, hit)
                    refs.append(
                        DiscussionRef(
                            external_id=object_id,
                            raw_data=hit,
                            source_id=source_id,
                        )
                    )

                logger.info("hackernews.discussions_collected count=%d", len(hits))
                self._update_last_fetched(engine, source_id)

        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        """Normalize one HN hit into silver rows.

        For stories with a URL: creates SilverContent via ensure_content, then upserts discussion.
        For self-posts (Ask HN, Show HN without URL): creates SilverContent with text populated
        immediately via _ensure_self_post_content, then upserts discussion.
        """
        hit = ref_data
        ext_id = str(hit.get("objectID", ""))
        if not ext_id:
            return

        hn_url = f"https://news.ycombinator.com/item?id={ext_id}"

        if hit.get("url"):
            # Normal story with external URL
            story_url = hit["url"]
            content_id = ensure_content(conn, str(story_url))
        else:
            # Self-post (Ask HN, Show HN, etc.) — text lives in story_text
            story_url = hn_url
            story_text = str(hit.get("story_text", ""))
            content_id = self._ensure_self_post_content(conn, hn_url, story_text)

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
            score=hit.get("points", 0),
            comment_count=hit.get("num_comments", 0),
        )
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)

    def collect_comments(
        self,
        engine: sa.engine.Engine,
        config: HackernewsConfig,
        settings: Settings,
        batch_limit: int = 10,
    ) -> int:
        if batch_limit <= 0:
            return 0

        rows = self._query_pending_comments(engine, batch_limit)

        if not rows:
            logger.info("hackernews.no_pending_comments")
            return 0

        logger.info("hackernews.fetching_comments pending=%d", len(rows))
        rate_limit = settings.hn_rate_limit
        fetched = 0

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for row in rows:
                discussion_id = row.id
                ext_id = row.external_id

                url = f"{HN_ALGOLIA_BASE}/items/{ext_id}"
                time.sleep(rate_limit)

                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()

                    # Write raw API response to bronze before storing in silver
                    write_bronze(self.source_type, ext_id, "comments", json.dumps(data, ensure_ascii=False), "json")

                    children = data.get("children", [])
                    self._mark_comments_done(engine, discussion_id, ext_id, json.dumps(children), len(children))
                    fetched += 1
                except Exception:
                    logger.exception("hackernews.comments_fetch_failed story_id=%s", ext_id)
                    self._mark_comments_failed(engine, ext_id, f"fetch_error:{ext_id}")
                    continue

            logger.info("hackernews.comments_fetched fetched=%d total_pending=%d", fetched, len(rows))

        return fetched

    def search_by_url(
        self,
        url: str,
        engine: sa.engine.Engine,
        config: HackernewsConfig,
        settings: Settings,
    ) -> int:
        rate_limit = settings.hn_rate_limit
        new_count = 0

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            time.sleep(rate_limit)

            resp = client.get(
                f"{HN_ALGOLIA_BASE}/search",
                params={
                    "query": url,
                    "tags": "story",
                    "restrictSearchableAttributes": "url",
                },
            )
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
                    self.process_discussion(hit, conn, source_id)
                    new_count += 1

        return new_count
