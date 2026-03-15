"""Reddit JSON API collector using httpx."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
from tenacity import (
    RetryCallState,
    retry,
    stop_after_attempt,
    wait_exponential,
)

from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.collectors.reddit.config import RedditConfig
from aggre.settings import Settings
from aggre.urls import ensure_content
from aggre.utils.bronze import write_bronze
from aggre.utils.http import create_http_client

logger = logging.getLogger(__name__)

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "url", "content_text", "meta", "score", "comment_count")


def _should_retry(retry_state: RetryCallState) -> bool:
    exc = retry_state.outcome.exception()
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 503)


def _rate_limit_sleep(resp: httpx.Response, min_delay: float) -> None:
    """Adaptively sleep based on Reddit's rate-limit response headers."""
    remaining = resp.headers.get("x-ratelimit-remaining")
    reset = resp.headers.get("x-ratelimit-reset")
    if remaining is not None and reset is not None:
        remaining_f, reset_f = float(remaining), float(reset)
        if remaining_f <= 1:
            logger.warning("reddit.rate_limit_exhausted remaining=%s sleeping=%s", remaining_f, reset_f)
            time.sleep(reset_f)
        elif remaining_f < 5:
            delay = reset_f / remaining_f
            logger.info("reddit.rate_limit_low remaining=%s reset_in=%s sleeping=%s", remaining_f, reset_f, round(delay, 1))
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
def _fetch_json(client: httpx.Client, url: str) -> tuple[dict[str, object], httpx.Response]:
    """Fetch JSON from URL, respecting Retry-After on 429s."""
    resp = client.get(url)
    if resp.status_code == 429:  # pragma: no cover — rate limiting
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            logger.warning("reddit.429_retry_after url=%s retry_after=%s", url, retry_after)
            time.sleep(float(retry_after))
        resp.raise_for_status()
    resp.raise_for_status()
    return resp.json(), resp


class RedditCollector(BaseCollector):
    """Collect posts and comments from Reddit's public JSON API."""

    source_type = "reddit"

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: RedditConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        """Fetch post listings, write bronze, return references with source_ids."""
        refs: list[DiscussionRef] = []
        rate_limit = settings.reddit_rate_limit

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for reddit_source in config.sources:
                sub = reddit_source.subreddit
                logger.info("reddit.collecting subreddit=%s", sub)

                source_id = self._ensure_source(engine, sub, {"subreddit": sub})

                # Fetch hot + new listings, dedup by external_id
                posts_by_id: dict[str, dict[str, object]] = {}
                for sort in ("hot", "new"):
                    url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit={config.fetch_limit}"
                    time.sleep(rate_limit)
                    try:
                        data, resp = _fetch_json(client, url)
                        _rate_limit_sleep(resp, 0)
                    except Exception:  # pragma: no cover — network error
                        logger.exception("reddit.fetch_failed subreddit=%s sort=%s", sub, sort)
                        continue

                    for child in data.get("data", {}).get("children", []):
                        post_data = child.get("data", {})
                        ext_id = post_data.get("name")
                        if ext_id and ext_id not in posts_by_id:
                            posts_by_id[ext_id] = post_data

                # Write bronze and build refs
                for ext_id, post_data in posts_by_id.items():
                    self._write_bronze(ext_id, post_data)
                    refs.append(
                        DiscussionRef(
                            external_id=ext_id,
                            raw_data=post_data,
                            source_id=source_id,
                        )
                    )

                logger.info("reddit.discussions_collected subreddit=%s count=%d", sub, len(posts_by_id))
                self._update_last_fetched(engine, source_id)

        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        """Normalize one bronze Reddit post into silver rows."""
        post_data = ref_data
        ext_id = post_data.get("name", "")
        subreddit = post_data.get("subreddit", "")

        published_at = datetime.fromtimestamp(post_data.get("created_utc", 0), tz=UTC).isoformat()

        permalink = f"https://reddit.com{post_data.get('permalink', '')}"
        is_self = post_data.get("is_self", True)
        post_url = post_data.get("url", "")

        content_id = None
        if not is_self and post_url and "reddit.com" not in post_url:
            # Link post: ensure content for the external URL
            content_id = ensure_content(conn, post_url)
        else:
            # Self-post: create SilverContent with text populated
            selftext = post_data.get("selftext", "")
            if selftext:
                content_id = self._ensure_self_post_content(conn, permalink, selftext)

        meta = json.dumps(
            {
                "subreddit": subreddit,
                "flair": post_data.get("link_flair_text"),
            }
        )

        values = dict(
            source_id=source_id,
            source_type="reddit",
            external_id=ext_id,
            title=post_data.get("title"),
            author=post_data.get("author"),
            url=permalink,
            content_text=post_data.get("selftext", ""),
            published_at=published_at,
            meta=meta,
            content_id=content_id,
            score=post_data.get("score", 0),
            comment_count=post_data.get("num_comments", 0),
        )
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)

    def collect_comments(
        self,
        engine: sa.engine.Engine,
        config: RedditConfig,
        settings: Settings,
        batch_limit: int = 10,
    ) -> int:
        """Fetch comments for posts with comments_json=NULL, up to batch_limit posts."""
        if batch_limit <= 0:
            return 0

        rows = self._query_pending_comments(engine, batch_limit)

        if not rows:
            logger.info("reddit.no_pending_comments")
            return 0

        logger.info("reddit.fetching_comments pending=%d", len(rows))
        rate_limit = settings.reddit_rate_limit
        fetched = 0

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for row in rows:
                discussion_id = row.id
                ext_id = row.external_id
                meta = json.loads(row.meta) if row.meta else {}
                subreddit = meta.get("subreddit", "")

                post_id = ext_id.removeprefix("t3_")
                url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
                time.sleep(rate_limit)
                try:
                    data, resp = _fetch_json(client, url)
                    _rate_limit_sleep(resp, 0)

                    # Write raw API response to bronze before storing in silver
                    write_bronze(self.source_type, ext_id, "comments", json.dumps(data, ensure_ascii=False), "json")

                    comments_json = None
                    comment_count = 0
                    if len(data) >= 2:
                        comment_children = data[1].get("data", {}).get("children", [])
                        comments_json = json.dumps(comment_children)
                        comment_count = len(comment_children)

                    self._mark_comments_done(engine, discussion_id, ext_id, comments_json, comment_count)
                    fetched += 1
                except Exception:  # pragma: no cover — network error during comments fetch
                    logger.exception("reddit.comments_fetch_failed post_id=%s", ext_id)
                    self._mark_comments_failed(engine, ext_id, f"fetch_error:{ext_id}")
                    continue

            logger.info("reddit.comments_fetched fetched=%d total_pending=%d", fetched, len(rows))

        return fetched

    def fetch_discussion_comments(
        self,
        engine: sa.engine.Engine,
        discussion_id: int,
        external_id: str,
        meta_json: str | None,
        settings: Settings,
    ) -> None:
        """Fetch and store comments for a single discussion."""
        meta = json.loads(meta_json) if meta_json else {}
        subreddit = meta.get("subreddit", "")
        post_id = external_id.removeprefix("t3_")
        url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"

        rate_limit = settings.reddit_rate_limit
        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            time.sleep(rate_limit)
            data, resp = _fetch_json(client, url)
            _rate_limit_sleep(resp, 0)
            write_bronze(self.source_type, external_id, "comments", json.dumps(data, ensure_ascii=False), "json")
            comments_json = None
            comment_count = 0
            if len(data) >= 2:
                comment_children = data[1].get("data", {}).get("children", [])
                comments_json = json.dumps(comment_children)
                comment_count = len(comment_children)
            self._mark_comments_done(engine, discussion_id, comments_json, comment_count)
