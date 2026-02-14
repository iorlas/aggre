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
from aggre.db import BronzeComment, BronzePost, SilverComment, SilverPost, Source

USER_AGENT = "linux:aggre:v0.1.0 (content-aggregator)"


def _should_retry(retry_state) -> bool:
    exc = retry_state.outcome.exception()
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 503)


def _rate_limit_sleep(resp: httpx.Response, min_delay: float, log: structlog.stdlib.BoundLogger) -> None:
    """Adaptively sleep based on Reddit's rate-limit response headers."""
    remaining = resp.headers.get("x-ratelimit-remaining")
    reset = resp.headers.get("x-ratelimit-reset")
    if remaining is not None and reset is not None:
        remaining_f, reset_f = float(remaining), float(reset)
        if remaining_f <= 1:
            log.warning("reddit.rate_limit_exhausted", remaining=remaining_f, sleeping=reset_f)
            time.sleep(reset_f)
        elif remaining_f < 5:
            delay = reset_f / remaining_f
            log.info("reddit.rate_limit_low", remaining=remaining_f, reset_in=reset_f, sleeping=round(delay, 1))
            time.sleep(delay)
        else:
            time.sleep(min_delay)
    else:
        time.sleep(min_delay)


@retry(
    retry=_should_retry,
    stop=stop_after_attempt(7),
    wait=wait_exponential(multiplier=2, min=4, max=120),
)
def _fetch_json(client: httpx.Client, url: str, log: structlog.stdlib.BoundLogger) -> tuple[dict, httpx.Response]:
    """Fetch JSON from URL, respecting Retry-After on 429s."""
    resp = client.get(url)
    if resp.status_code == 429:
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            log.warning("reddit.429_retry_after", url=url, retry_after=retry_after)
            time.sleep(float(retry_after))
        resp.raise_for_status()
    resp.raise_for_status()
    return resp.json(), resp


class RedditCollector:
    """Collect posts and comments from Reddit's public JSON API."""

    def collect(self, engine: sa.engine.Engine, config: AppConfig, log: structlog.stdlib.BoundLogger) -> int:
        """Fetch post listings only. Comments are fetched separately via collect_comments()."""
        total_new = 0
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
        rate_limit = config.settings.reddit_rate_limit

        try:
            for reddit_source in config.reddit:
                sub = reddit_source.subreddit
                log.info("reddit.collecting", subreddit=sub)

                source_id = self._ensure_source(engine, sub)

                # Fetch hot + new listings, dedup by external_id
                posts_by_id: dict[str, dict] = {}
                for sort in ("hot", "new"):
                    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit=100"
                    time.sleep(rate_limit)
                    try:
                        data, resp = _fetch_json(client, url, log)
                        _rate_limit_sleep(resp, 0, log)
                    except Exception:
                        log.exception("reddit.fetch_failed", subreddit=sub, sort=sort)
                        continue

                    for child in data.get("data", {}).get("children", []):
                        post_data = child.get("data", {})
                        ext_id = post_data.get("name")
                        if ext_id and ext_id not in posts_by_id:
                            posts_by_id[ext_id] = post_data

                # Store posts with comments_status: pending
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

                # Update last_fetched_at
                with engine.begin() as conn:
                    conn.execute(sa.update(Source).where(Source.id == source_id).values(last_fetched_at=datetime.now(UTC).isoformat()))
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
        """Fetch comments for posts with comments_status='pending', up to batch_limit posts."""
        if batch_limit <= 0:
            return 0

        # Find pending posts
        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverPost.id, SilverPost.external_id, SilverPost.meta)
                .where(
                    SilverPost.source_type == "reddit",
                    SilverPost.meta.like('%"comments_status": "pending"%'),
                )
                .limit(batch_limit)
            ).fetchall()

        if not rows:
            log.info("reddit.no_pending_comments")
            return 0

        log.info("reddit.fetching_comments", pending=len(rows))
        rate_limit = config.settings.reddit_rate_limit
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
        fetched = 0

        try:
            for row in rows:
                ci_id = row.id
                ext_id = row.external_id
                meta = json.loads(row.meta) if row.meta else {}
                subreddit = meta.get("subreddit", "")

                post_id = ext_id.removeprefix("t3_")
                url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
                time.sleep(rate_limit)
                try:
                    data, resp = _fetch_json(client, url, log)
                    _rate_limit_sleep(resp, 0, log)
                except Exception:
                    log.exception("reddit.comments_fetch_failed", post_id=ext_id)
                    continue

                with engine.begin() as conn:
                    if len(data) >= 2:
                        comment_children = data[1].get("data", {}).get("children", [])
                        self._walk_comments(conn, ci_id, comment_children, depth=0)

                    # Mark as done
                    meta["comments_status"] = "done"
                    conn.execute(
                        sa.update(SilverPost)
                        .where(SilverPost.id == ci_id)
                        .values(meta=json.dumps(meta))
                    )
                    fetched += 1

            log.info("reddit.comments_fetched", fetched=fetched, total_pending=len(rows))
        finally:
            client.close()

        return fetched

    def _ensure_source(self, engine: sa.engine.Engine, subreddit: str) -> int:
        with engine.begin() as conn:
            row = conn.execute(
                sa.select(Source.id).where(
                    Source.type == "reddit",
                    Source.name == subreddit,
                )
            ).first()
            if row:
                return row[0]
            result = conn.execute(
                sa.insert(Source).values(
                    type="reddit",
                    name=subreddit,
                    config=json.dumps({"subreddit": subreddit}),
                )
            )
            return result.lastrowid

    def _store_raw_item(self, conn: sa.Connection, ext_id: str, post_data: dict) -> int | None:
        """Insert raw item. Returns id if new, None if duplicate."""
        result = conn.execute(
            sa.insert(BronzePost)
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
        """Insert content item with comments_status='pending'. Returns id if new, None if duplicate."""
        published_at = datetime.fromtimestamp(post_data.get("created_utc", 0), tz=UTC).isoformat()
        meta = json.dumps(
            {
                "subreddit": subreddit,
                "score": post_data.get("score", 0),
                "num_comments": post_data.get("num_comments", 0),
                "flair": post_data.get("link_flair_text"),
                "comments_status": "pending",
            }
        )
        result = conn.execute(
            sa.insert(SilverPost)
            .prefix_with("OR IGNORE")
            .values(
                source_id=source_id,
                bronze_post_id=raw_id,
                source_type="reddit",
                external_id=ext_id,
                title=post_data.get("title"),
                author=post_data.get("author"),
                url=f"https://reddit.com{post_data.get('permalink', '')}",
                content_text=post_data.get("selftext", ""),
                published_at=published_at,
                meta=meta,
            )
        )
        if result.rowcount == 0:
            return None
        return result.lastrowid

    def _walk_comments(self, conn: sa.Connection, silver_post_id: int | None, children: list, depth: int) -> None:
        for child in children:
            if child.get("kind") != "t1":
                continue
            comment = child.get("data", {})
            ext_id = comment.get("name")
            if not ext_id:
                continue

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

            # Store silver comment
            created_at = datetime.fromtimestamp(comment.get("created_utc", 0), tz=UTC).isoformat()
            conn.execute(
                sa.insert(SilverComment)
                .prefix_with("OR IGNORE")
                .values(
                    silver_post_id=silver_post_id,
                    bronze_comment_id=bronze_comment_id,
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
                self._walk_comments(conn, silver_post_id, reply_children, depth=depth + 1)
