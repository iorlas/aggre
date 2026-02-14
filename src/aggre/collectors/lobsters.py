"""Lobsters collector using the native JSON API."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
import sqlalchemy as sa
import structlog

from aggre.config import AppConfig
from aggre.db import BronzeComment, BronzePost, SilverComment, SilverPost, Source

LOBSTERS_BASE = "https://lobste.rs"
USER_AGENT = "aggre/0.1.0 (content-aggregator)"


class LobstersCollector:
    """Collect stories and comments from Lobsters via the JSON API."""

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
                        if raw_id is not None:
                            ci_id = self._store_content_item(conn, source_id, raw_id, short_id, story)
                            if ci_id is not None:
                                total_new += 1

                log.info("lobsters.posts_stored", new=total_new, total_seen=len(stories_by_id))

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
                    SilverPost.source_type == "lobsters",
                    SilverPost.meta.like('%"comments_status": "pending"%'),
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
                ci_id = row.id
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
                    for comment in comments:
                        self._store_comment(conn, ci_id, comment)

                    meta = json.loads(row.meta) if row.meta else {}
                    meta["comments_status"] = "done"
                    conn.execute(
                        sa.update(SilverPost)
                        .where(SilverPost.id == ci_id)
                        .values(meta=json.dumps(meta))
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

            try:
                resp = client.get(search_url)
                resp.raise_for_status()
                stories = resp.json()
            except Exception:
                log.exception("lobsters.search_failed", url=url)
                return 0

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
                    if raw_id is not None:
                        ci_id = self._store_content_item(conn, source_id, raw_id, short_id, story)
                        if ci_id is not None:
                            new_count += 1
        finally:
            client.close()

        return new_count

    def _ensure_source(self, engine: sa.engine.Engine, name: str) -> int:
        with engine.begin() as conn:
            row = conn.execute(
                sa.select(Source.id).where(Source.type == "lobsters", Source.name == name)
            ).first()
            if row:
                return row[0]
            result = conn.execute(
                sa.insert(Source).values(
                    type="lobsters",
                    name=name,
                    config=json.dumps({"name": name}),
                )
            )
            return result.lastrowid

    def _store_raw_item(self, conn: sa.Connection, short_id: str, story: dict) -> int | None:
        result = conn.execute(
            sa.insert(BronzePost)
            .prefix_with("OR IGNORE")
            .values(
                source_type="lobsters",
                external_id=short_id,
                raw_data=json.dumps(story),
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid

    def _store_content_item(
        self, conn: sa.Connection, source_id: int, raw_id: int, short_id: str, story: dict,
    ) -> int | None:
        story_url = story.get("url") or story.get("comments_url", "")
        comments_url = story.get("comments_url", "")

        meta = json.dumps({
            "score": story.get("score", 0),
            "comment_count": story.get("comment_count", 0),
            "tags": story.get("tags", []),
            "comments_status": "pending",
            "lobsters_url": comments_url,
        })

        result = conn.execute(
            sa.insert(SilverPost)
            .prefix_with("OR IGNORE")
            .values(
                source_id=source_id,
                bronze_post_id=raw_id,
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
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid

    def _store_comment(self, conn: sa.Connection, silver_post_id: int, comment: dict) -> None:
        ext_id = comment.get("short_id", "")
        if not ext_id:
            return

        # Store raw comment
        rc_result = conn.execute(
            sa.insert(BronzeComment)
            .prefix_with("OR IGNORE")
            .values(
                bronze_post_id=None,
                external_id=ext_id,
                raw_data=json.dumps(comment),
            )
        )
        bronze_comment_id = rc_result.lastrowid if rc_result.rowcount > 0 else None

        # Lobsters uses indent_level (1-based), convert to 0-based depth
        indent_level = comment.get("indent_level", 1)
        depth = max(0, indent_level - 1)

        # Author can be a string or nested object
        commenting_user = comment.get("commenting_user")
        if isinstance(commenting_user, dict):
            author = commenting_user.get("username")
        else:
            author = commenting_user

        conn.execute(
            sa.insert(SilverComment)
            .prefix_with("OR IGNORE")
            .values(
                silver_post_id=silver_post_id,
                bronze_comment_id=bronze_comment_id,
                external_id=ext_id,
                author=author,
                body=comment.get("comment"),
                score=comment.get("score"),
                parent_id=comment.get("parent_comment"),
                depth=depth,
                created_at=comment.get("created_at"),
            )
        )
