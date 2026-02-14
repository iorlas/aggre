"""Hacker News collector using the Algolia API."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
import structlog

from aggre.config import AppConfig
from aggre.db import BronzeComment, BronzePost, SilverComment, SilverPost, Source

HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


class HackernewsCollector:
    """Collect stories and comments from Hacker News via the Algolia API."""

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        if not config.hackernews:
            return 0

        total_new = 0
        rate_limit = config.settings.hn_rate_limit
        client = httpx.Client(timeout=30.0)

        try:
            for hn_source in config.hackernews:
                log.info("hackernews.collecting", name=hn_source.name)
                source_id = self._ensure_source(engine, hn_source.name)

                url = f"{HN_ALGOLIA_BASE}/search_by_date?tags=story,front_page&hitsPerPage={config.settings.fetch_limit}"
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

                        raw_id = self._store_raw_item(conn, object_id, hit)
                        if raw_id is not None:
                            ci_id = self._store_content_item(conn, source_id, raw_id, object_id, hit)
                            if ci_id is not None:
                                total_new += 1

                log.info("hackernews.posts_stored", new=total_new, total_hits=len(hits))

                with engine.begin() as conn:
                    conn.execute(
                        sa.update(Source).where(Source.id == source_id)
                        .values(last_fetched_at=datetime.now(UTC).isoformat())
                    )
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
                sa.select(SilverPost.id, SilverPost.external_id, SilverPost.meta)
                .where(
                    SilverPost.source_type == "hackernews",
                    SilverPost.meta.like('%"comments_status": "pending"%'),
                )
                .limit(batch_limit)
            ).fetchall()

        if not rows:
            log.info("hackernews.no_pending_comments")
            return 0

        log.info("hackernews.fetching_comments", pending=len(rows))
        rate_limit = config.settings.hn_rate_limit
        client = httpx.Client(timeout=30.0)
        fetched = 0

        try:
            for row in rows:
                ci_id = row.id
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

                with engine.begin() as conn:
                    children = data.get("children", [])
                    self._walk_comments(conn, ci_id, children, depth=0)

                    meta = json.loads(row.meta) if row.meta else {}
                    meta["comments_status"] = "done"
                    conn.execute(
                        sa.update(SilverPost)
                        .where(SilverPost.id == ci_id)
                        .values(meta=json.dumps(meta))
                    )
                    fetched += 1

            log.info("hackernews.comments_fetched", fetched=fetched, total_pending=len(rows))
        finally:
            client.close()

        return fetched

    def search_by_url(
        self, url: str, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger,
    ) -> int:
        rate_limit = config.settings.hn_rate_limit
        client = httpx.Client(timeout=30.0)
        new_count = 0

        try:
            search_url = f"{HN_ALGOLIA_BASE}/search?query={url}&tags=story&restrictSearchableAttributes=url"
            time.sleep(rate_limit)

            try:
                resp = client.get(search_url)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                log.exception("hackernews.search_failed", url=url)
                return 0

            source_id = self._ensure_source(engine, "Hacker News")

            hits = data.get("hits", [])
            with engine.begin() as conn:
                for hit in hits:
                    object_id = str(hit.get("objectID", ""))
                    if not object_id:
                        continue

                    raw_id = self._store_raw_item(conn, object_id, hit)
                    if raw_id is not None:
                        ci_id = self._store_content_item(conn, source_id, raw_id, object_id, hit)
                        if ci_id is not None:
                            new_count += 1
        finally:
            client.close()

        return new_count

    def _ensure_source(self, engine: sa.engine.Engine, name: str) -> int:
        with engine.begin() as conn:
            row = conn.execute(
                sa.select(Source.id).where(Source.type == "hackernews", Source.name == name)
            ).first()
            if row:
                return row[0]
            result = conn.execute(
                sa.insert(Source).values(
                    type="hackernews",
                    name=name,
                    config=json.dumps({"name": name}),
                )
            )
            return result.lastrowid

    def _store_raw_item(self, conn: sa.Connection, ext_id: str, hit: dict) -> int | None:
        result = conn.execute(
            sa.insert(BronzePost)
            .prefix_with("OR IGNORE")
            .values(
                source_type="hackernews",
                external_id=ext_id,
                raw_data=json.dumps(hit),
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid

    def _store_content_item(
        self, conn: sa.Connection, source_id: int, raw_id: int, ext_id: str, hit: dict,
    ) -> int | None:
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={ext_id}"
        hn_url = f"https://news.ycombinator.com/item?id={ext_id}"

        created_at_str = hit.get("created_at")
        published_at = created_at_str if created_at_str else None

        meta = json.dumps({
            "points": hit.get("points", 0),
            "num_comments": hit.get("num_comments", 0),
            "comments_status": "pending",
            "hn_url": hn_url,
        })

        result = conn.execute(
            sa.insert(SilverPost)
            .prefix_with("OR IGNORE")
            .values(
                source_id=source_id,
                bronze_post_id=raw_id,
                source_type="hackernews",
                external_id=ext_id,
                title=hit.get("title"),
                author=hit.get("author"),
                url=story_url,
                published_at=published_at,
                meta=meta,
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid

    def _walk_comments(self, conn: sa.Connection, silver_post_id: int, children: list, depth: int) -> None:
        for child in children:
            ext_id = str(child.get("id", ""))
            if not ext_id:
                continue

            # Store raw comment
            rc_result = conn.execute(
                sa.insert(BronzeComment)
                .prefix_with("OR IGNORE")
                .values(
                    bronze_post_id=None,
                    external_id=ext_id,
                    raw_data=json.dumps(child),
                )
            )
            bronze_comment_id = rc_result.lastrowid if rc_result.rowcount > 0 else None

            created_at = child.get("created_at")

            conn.execute(
                sa.insert(SilverComment)
                .prefix_with("OR IGNORE")
                .values(
                    silver_post_id=silver_post_id,
                    bronze_comment_id=bronze_comment_id,
                    external_id=ext_id,
                    author=child.get("author"),
                    body=child.get("text"),
                    score=child.get("points"),
                    parent_id=str(child.get("parent_id", "")) or None,
                    depth=depth,
                    created_at=created_at,
                )
            )

            # Recurse into children
            sub_children = child.get("children", [])
            if sub_children:
                self._walk_comments(conn, silver_post_id, sub_children, depth=depth + 1)
