"""Reddit JSON API collector using httpx."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from aggre.config import AppConfig
from aggre.db import content_items, raw_comments, raw_items, reddit_comments, sources

USER_AGENT = "aggre/0.1 content-aggregator"


def _should_retry(retry_state) -> bool:
    exc = retry_state.outcome.exception()
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 503)


@retry(
    retry=_should_retry,
    stop=stop_after_attempt(7),
    wait=wait_exponential(multiplier=2, min=4, max=120),
)
def _fetch_json(client: httpx.Client, url: str) -> dict:
    resp = client.get(url)
    resp.raise_for_status()
    return resp.json()


class RedditCollector:
    """Collect posts and comments from Reddit's public JSON API."""

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        total_new = 0
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)

        try:
            for reddit_source in config.reddit:
                sub = reddit_source.subreddit
                log.info("reddit.collecting", subreddit=sub)

                source_id = self._ensure_source(engine, sub)

                # Fetch hot + new listings, dedup by external_id
                posts_by_id: dict[str, dict] = {}
                rate_limit = config.settings.reddit_rate_limit
                for sort in ("hot", "new"):
                    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit=100"
                    time.sleep(rate_limit)
                    try:
                        data = _fetch_json(client, url)
                    except Exception:
                        log.exception("reddit.fetch_failed", subreddit=sub, sort=sort)
                        continue

                    for child in data.get("data", {}).get("children", []):
                        post_data = child.get("data", {})
                        ext_id = post_data.get("name")
                        if ext_id and ext_id not in posts_by_id:
                            posts_by_id[ext_id] = post_data

                # Store posts
                new_post_ids: list[str] = []
                with engine.begin() as conn:
                    for ext_id, post_data in posts_by_id.items():
                        raw_id = self._store_raw_item(conn, ext_id, post_data)
                        if raw_id is not None:
                            ci_id = self._store_content_item(conn, source_id, raw_id, ext_id, post_data, sub)
                            if ci_id is not None:
                                new_post_ids.append(ext_id)
                                total_new += 1

                log.info("reddit.posts_stored", subreddit=sub, new=len(new_post_ids), total_seen=len(posts_by_id))

                # Fetch comments for new posts
                for ext_id in new_post_ids:
                    post_id = ext_id.removeprefix("t3_")
                    url = f"https://www.reddit.com/r/{sub}/comments/{post_id}.json"
                    time.sleep(rate_limit)
                    try:
                        data = _fetch_json(client, url)
                    except Exception:
                        log.exception("reddit.comments_fetch_failed", subreddit=sub, post_id=ext_id)
                        continue

                    # Look up content_item_id for linking comments
                    with engine.begin() as conn:
                        ci_row = conn.execute(
                            sa.select(content_items.c.id).where(
                                content_items.c.source_type == "reddit",
                                content_items.c.external_id == ext_id,
                            )
                        ).first()
                        ci_id = ci_row[0] if ci_row else None

                        if len(data) >= 2:
                            comment_children = data[1].get("data", {}).get("children", [])
                            self._walk_comments(conn, ci_id, comment_children, depth=0)

                # Update last_fetched_at
                with engine.begin() as conn:
                    conn.execute(sa.update(sources).where(sources.c.id == source_id).values(last_fetched_at=datetime.now(UTC).isoformat()))
        finally:
            client.close()

        return total_new

    def _ensure_source(self, engine: sa.engine.Engine, subreddit: str) -> int:
        with engine.begin() as conn:
            row = conn.execute(
                sa.select(sources.c.id).where(
                    sources.c.type == "reddit",
                    sources.c.name == subreddit,
                )
            ).first()
            if row:
                return row[0]
            result = conn.execute(
                sa.insert(sources).values(
                    type="reddit",
                    name=subreddit,
                    config=json.dumps({"subreddit": subreddit}),
                )
            )
            return result.lastrowid

    def _store_raw_item(self, conn: sa.Connection, ext_id: str, post_data: dict) -> int | None:
        """Insert raw item. Returns id if new, None if duplicate."""
        result = conn.execute(
            sa.insert(raw_items)
            .prefix_with("OR IGNORE")
            .values(
                source_type="reddit",
                external_id=ext_id,
                raw_data=json.dumps(post_data),
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid

    def _store_content_item(
        self,
        conn: sa.Connection,
        source_id: int,
        raw_id: int,
        ext_id: str,
        post_data: dict,
        subreddit: str,
    ) -> int | None:
        """Insert content item. Returns id if new, None if duplicate."""
        published_at = datetime.fromtimestamp(post_data.get("created_utc", 0), tz=UTC).isoformat()
        meta = json.dumps(
            {
                "subreddit": subreddit,
                "score": post_data.get("score", 0),
                "num_comments": post_data.get("num_comments", 0),
                "flair": post_data.get("link_flair_text"),
            }
        )
        result = conn.execute(
            sa.insert(content_items)
            .prefix_with("OR IGNORE")
            .values(
                source_id=source_id,
                raw_item_id=raw_id,
                source_type="reddit",
                external_id=ext_id,
                title=post_data.get("title"),
                author=post_data.get("author"),
                url=f"https://reddit.com{post_data.get('permalink', '')}",
                content_text=post_data.get("selftext", ""),
                published_at=published_at,
                metadata=meta,
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid

    def _walk_comments(self, conn: sa.Connection, content_item_id: int | None, children: list, depth: int) -> None:
        for child in children:
            if child.get("kind") != "t1":
                continue
            comment = child.get("data", {})
            ext_id = comment.get("name")
            if not ext_id:
                continue

            # Store raw comment
            rc_result = conn.execute(
                sa.insert(raw_comments)
                .prefix_with("OR IGNORE")
                .values(
                    raw_item_id=None,
                    external_id=ext_id,
                    raw_data=json.dumps(comment),
                )
            )
            raw_comment_id = rc_result.lastrowid if rc_result.rowcount > 0 else None

            # Store reddit comment
            created_at = datetime.fromtimestamp(comment.get("created_utc", 0), tz=UTC).isoformat()
            conn.execute(
                sa.insert(reddit_comments)
                .prefix_with("OR IGNORE")
                .values(
                    content_item_id=content_item_id,
                    raw_comment_id=raw_comment_id,
                    external_id=ext_id,
                    author=comment.get("author"),
                    body=comment.get("body"),
                    score=comment.get("score"),
                    parent_id=comment.get("parent_id"),
                    depth=depth,
                    created_at=created_at,
                )
            )

            # Recurse into replies
            replies = comment.get("replies")
            if isinstance(replies, dict):
                reply_children = replies.get("data", {}).get("children", [])
                self._walk_comments(conn, content_item_id, reply_children, depth=depth + 1)
