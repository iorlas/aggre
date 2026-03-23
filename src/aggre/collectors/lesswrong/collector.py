"""LessWrong collector using the GraphQL API."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.urls import ensure_content
from aggre.utils.http import create_http_client

if TYPE_CHECKING:
    import sqlalchemy as sa

    from aggre.collectors.lesswrong.config import LesswrongConfig
    from aggre.settings import Settings

logger = logging.getLogger(__name__)

LESSWRONG_GRAPHQL_URL = "https://www.lesswrong.com/graphql"

# Columns to update on re-insert (scores/titles always fresh)
_UPSERT_COLS = ("title", "author", "url", "meta", "score", "comment_count")

_POSTS_QUERY_TEMPLATE = """{
  posts(input: {terms: {view: "%s", limit: %d, af: %s}}) {
    results {
      _id
      title
      slug
      pageUrl
      postedAt
      baseScore
      voteCount
      commentCount
      af
      url
      user {
        displayName
      }
      tags {
        name
      }
    }
  }
}"""


class LesswrongCollector(BaseCollector):
    """Collect posts from LessWrong via the GraphQL API."""

    source_type = "lesswrong"

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: LesswrongConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        """Fetch LessWrong posts, write bronze, return references."""
        if not config.sources:
            return []

        refs: list[DiscussionRef] = []
        rate_limit = getattr(settings, "lesswrong_rate_limit", 1.0)

        with create_http_client(proxy_url=settings.proxy_url or None) as client:
            for lw_source in config.sources:
                logger.info("lesswrong.collecting name=%s", lw_source.name)
                source_id = self._ensure_source(engine, lw_source.name)

                time.sleep(rate_limit)

                af_str = "true" if lw_source.alignment_forum else "false"
                query_str = _POSTS_QUERY_TEMPLATE % (
                    lw_source.view,
                    config.fetch_limit,
                    af_str,
                )

                try:
                    resp = client.post(
                        LESSWRONG_GRAPHQL_URL,
                        json={"query": query_str},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception:
                    logger.exception("lesswrong.fetch_failed")
                    continue

                posts = data.get("data", {}).get("posts", {}).get("results", [])

                for post in posts:
                    base_score = post.get("baseScore", 0) or 0
                    if base_score < lw_source.min_karma:
                        continue

                    post_id = post.get("_id", "")
                    if not post_id:
                        continue

                    self._write_bronze(post_id, post)
                    refs.append(
                        DiscussionRef(
                            external_id=post_id,
                            raw_data=post,
                            source_id=source_id,
                        )
                    )

                logger.info(
                    "lesswrong.discussions_collected count=%d",
                    len(posts),
                )
                self._update_last_fetched(engine, source_id)

        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        """Normalize one LessWrong post into silver rows."""
        post = ref_data
        ext_id = post.get("_id", "")
        if not ext_id:
            return

        page_url = post.get("pageUrl", "")
        link_url = post.get("url")

        if link_url:
            # Link post — ensure content for the external URL
            content_id = ensure_content(conn, str(link_url))
        else:
            # Native essay — webpage pipeline will fetch the full page
            content_id = ensure_content(conn, str(page_url))

        published_at = post.get("postedAt")

        meta = json.dumps(
            {
                "tags": [t["name"] for t in post.get("tags") or []],
                "af": post.get("af", False),
                "vote_count": post.get("voteCount", 0),
            }
        )

        author = (post.get("user") or {}).get("displayName", "")

        values = {
            "source_id": source_id,
            "source_type": "lesswrong",
            "external_id": ext_id,
            "title": post.get("title"),
            "author": author,
            "url": page_url,
            "published_at": published_at,
            "meta": meta,
            "content_id": content_id,
            "score": post.get("baseScore", 0),
            "comment_count": post.get("commentCount", 0),
        }
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
