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

from aggre.collectors.base import BaseCollector
from aggre.collectors.reddit.config import RedditConfig
from aggre.http import create_http_client
from aggre.settings import Settings
from aggre.statuses import CommentsStatus
from aggre.urls import ensure_content

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "url", "content_text", "meta", "score", "comment_count")


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


class RedditCollector(BaseCollector):
    """Collect posts and comments from Reddit's public JSON API."""

    source_type = "reddit"

    def collect(self, engine: sa.engine.Engine, config: RedditConfig, settings: Settings, log: structlog.stdlib.BoundLogger) -> int:
        """Fetch post listings only. Comments are fetched separately via collect_comments()."""
        total_new = 0
        client = create_http_client(proxy_url=settings.proxy_url or None)
        rate_limit = settings.reddit_rate_limit

        try:
            for reddit_source in config.sources:
                sub = reddit_source.subreddit
                log.info("reddit.collecting", subreddit=sub)

                source_id = self._ensure_source(engine, sub, {"subreddit": sub})

                # Fetch hot + new listings, dedup by external_id
                posts_by_id: dict[str, dict] = {}
                for sort in ("hot", "new"):
                    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={config.fetch_limit}"
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

                # Store posts
                new_post_ids: list[str] = []
                with engine.begin() as conn:
                    for ext_id, post_data in posts_by_id.items():
                        raw_id = self._store_raw_item(conn, ext_id, post_data)
                        discussion_id = self._store_discussion(conn, source_id, raw_id, ext_id, post_data, sub)
                        if discussion_id is not None:
                            new_post_ids.append(ext_id)
                            total_new += 1

                log.info("reddit.discussions_stored", subreddit=sub, new=len(new_post_ids), total_seen=len(posts_by_id))
                self._update_last_fetched(engine, source_id)
        finally:
            client.close()

        return total_new

    def collect_comments(
        self,
        engine: sa.engine.Engine,
        config: RedditConfig,
        settings: Settings,
        log: structlog.stdlib.BoundLogger,
        batch_limit: int = 10,
    ) -> int:
        """Fetch comments for posts with comments_status='pending', up to batch_limit posts."""
        if batch_limit <= 0:
            return 0

        rows = self._query_pending_comments(engine, batch_limit)

        if not rows:
            log.info("reddit.no_pending_comments")
            return 0

        log.info("reddit.fetching_comments", pending=len(rows))
        rate_limit = settings.reddit_rate_limit
        client = create_http_client(proxy_url=settings.proxy_url or None)
        fetched = 0

        try:
            for row in rows:
                discussion_id = row.id
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

                comments_json = None
                comment_count = 0
                if len(data) >= 2:
                    comment_children = data[1].get("data", {}).get("children", [])
                    comments_json = json.dumps(comment_children)
                    comment_count = len(comment_children)

                self._mark_comments_done(engine, discussion_id, comments_json, comment_count)
                fetched += 1

            log.info("reddit.comments_fetched", fetched=fetched, total_pending=len(rows))
        finally:
            client.close()

        return fetched

    def _store_discussion(
        self,
        conn: sa.Connection,
        source_id: int,
        raw_id: int | None,
        ext_id: str,
        post_data: dict,
        subreddit: str,
    ) -> int | None:
        published_at = datetime.fromtimestamp(post_data.get("created_utc", 0), tz=UTC).isoformat()

        content_id = None
        if not post_data.get("is_self", True):
            post_url = post_data.get("url", "")
            if post_url and "reddit.com" not in post_url:
                content_id = ensure_content(conn, post_url)

        meta = json.dumps(
            {
                "subreddit": subreddit,
                "flair": post_data.get("link_flair_text"),
            }
        )

        values = dict(
            source_id=source_id,
            bronze_discussion_id=raw_id,
            source_type="reddit",
            external_id=ext_id,
            title=post_data.get("title"),
            author=post_data.get("author"),
            url=f"https://reddit.com{post_data.get('permalink', '')}",
            content_text=post_data.get("selftext", ""),
            published_at=published_at,
            meta=meta,
            content_id=content_id,
            comments_status=CommentsStatus.PENDING,
            score=post_data.get("score", 0),
            comment_count=post_data.get("num_comments", 0),
        )
        return self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
