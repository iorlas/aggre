"""Lobsters collector using the native JSON API."""

from __future__ import annotations

import json
import logging
import time

import sqlalchemy as sa

from aggre.collectors.base import BaseCollector, DiscussionRef
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

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: LobstersConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        if not config.sources:
            return []

        refs: list[DiscussionRef] = []
        rate_limit = settings.lobsters_rate_limit

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for lob_source in config.sources:
                logger.info("lobsters.collecting name=%s", lob_source.name)
                source_id = self._ensure_source(engine, lob_source.name)

                urls: list[str] = []
                if lob_source.tags:
                    for tag in lob_source.tags:
                        for page in range(1, config.pages + 1):
                            urls.append(f"{LOBSTERS_BASE}/t/{tag}.json?page={page}")
                else:
                    for page in range(1, config.pages + 1):
                        urls.append(f"{LOBSTERS_BASE}/hottest.json?page={page}")
                        urls.append(f"{LOBSTERS_BASE}/newest.json?page={page}")

                stories_by_id: dict[str, dict[str, object]] = {}
                for url in urls:
                    time.sleep(rate_limit)
                    try:
                        resp = client.get(url)
                        resp.raise_for_status()
                        stories = resp.json()
                    except Exception:  # pragma: no cover — network error
                        logger.exception("lobsters.fetch_failed url=%s", url)
                        continue

                    for story in stories:
                        short_id = story.get("short_id")
                        if short_id and short_id not in stories_by_id:
                            stories_by_id[short_id] = story

                for short_id, story in stories_by_id.items():
                    self._write_bronze(short_id, story)
                    refs.append(DiscussionRef(external_id=short_id, raw_data=story, source_id=source_id))

                logger.info("lobsters.discussions_collected count=%d", len(stories_by_id))
                self._update_last_fetched(engine, source_id)

        return refs

    def process_discussion(
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
        elif not story.get("url") or story.get("url") == comments_url:  # pragma: no cover — self-post path
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
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)

    def fetch_discussion_comments(
        self,
        engine: sa.engine.Engine,
        discussion_id: int,
        external_id: str,
        meta_json: str | None,
        settings: Settings,
    ) -> None:
        """Fetch and store comments for a single discussion."""
        rate_limit = settings.lobsters_rate_limit
        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            time.sleep(rate_limit)
            url = f"{LOBSTERS_BASE}/s/{external_id}.json"
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            write_bronze(self.source_type, external_id, "comments", json.dumps(data, ensure_ascii=False), "json")
            comments = data.get("comments", [])
            self._mark_comments_done(engine, discussion_id, json.dumps(comments), len(comments))
