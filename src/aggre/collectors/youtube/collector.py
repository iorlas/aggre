"""YouTube metadata collector using yt-dlp."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.urls import ensure_content
from aggre.utils.ytdlp import VideoUnavailableError, YtDlpError, extract_channel_info

if TYPE_CHECKING:
    import sqlalchemy as sa

    from aggre.collectors.youtube.config import YoutubeConfig
    from aggre.settings import Settings

logger = logging.getLogger(__name__)

# Columns to update on re-insert (titles always fresh)
_UPSERT_COLS = ("title", "url", "meta", "published_at")


class YoutubeCollector(BaseCollector):
    """Fetches YouTube channel video metadata and stores entries in the database."""

    source_type = "youtube"

    def collect_discussions(
        self,
        engine: sa.engine.Engine,
        config: YoutubeConfig,
        settings: Settings,
        backfill: bool = False,
        source_ttl_minutes: int = 0,
    ) -> list[DiscussionRef]:
        """Fetch YouTube channel metadata via yt-dlp, write bronze, return references."""
        refs: list[DiscussionRef] = []

        for yt_source in config.sources:
            logger.info(
                "youtube.collecting name=%s channel_id=%s",
                yt_source.name,
                yt_source.channel_id,
            )

            source_id = self._ensure_source(engine, yt_source.name, {"channel_id": yt_source.channel_id})

            ttl = yt_source.fetch_interval_hours * 60 if yt_source.fetch_interval_hours else source_ttl_minutes
            if self._is_source_recent(engine, source_id, ttl):
                logger.info("youtube.source_skipped name=%s reason=recent", yt_source.name)
                continue

            fetch_limit = None if backfill else self._get_fetch_limit(engine, source_id, config.init_fetch_limit, config.fetch_limit)

            url = f"https://www.youtube.com/channel/{yt_source.channel_id}/videos"

            try:
                logger.info("youtube.fetching name=%s limit=%s", yt_source.name, fetch_limit)
                entries = extract_channel_info(url, proxy_url=settings.proxy_url, fetch_limit=fetch_limit)
                logger.info("youtube.fetched name=%s entries=%d", yt_source.name, len(entries))
            except (VideoUnavailableError, YtDlpError):
                logger.exception("youtube.fetch_error channel=%s", yt_source.name)
                continue

            total_entries = len(entries)
            refs_before = len(refs)

            for idx, entry in enumerate(entries, 1):
                if not entry:
                    continue

                external_id = entry.get("id")
                if not external_id:
                    logger.warning("skipping_entry_no_id channel=%s", yt_source.name)
                    continue

                logger.debug(
                    "youtube.processing_entry name=%s video_id=%s progress=%s",
                    yt_source.name,
                    external_id,
                    f"{idx}/{total_entries}",
                )

                # Attach channel metadata so process_discussion can use it
                raw_data = dict(entry)
                raw_data["_channel_id"] = yt_source.channel_id
                raw_data["_channel_name"] = yt_source.name

                self._write_bronze(external_id, raw_data)
                refs.append(
                    DiscussionRef(
                        external_id=external_id,
                        raw_data=raw_data,
                        source_id=source_id,
                    )
                )

            self._update_last_fetched(engine, source_id)
            logger.info("youtube.discussions_collected name=%s count=%d", yt_source.name, len(refs) - refs_before)

        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        """Normalize one YouTube entry into silver rows."""
        external_id = ref_data.get("id")
        if not external_id:
            return

        video_url = f"https://www.youtube.com/watch?v={external_id}"

        # Create content entry for the video URL
        content_id = ensure_content(conn, video_url)

        # Format upload_date from YYYYMMDD to YYYY-MM-DD if present
        published_at = None
        upload_date = ref_data.get("upload_date")
        if upload_date and len(upload_date) == 8:
            published_at = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
        elif upload_date:
            published_at = upload_date

        channel_id = ref_data.get("_channel_id", "")
        channel_name = ref_data.get("_channel_name", "")

        meta = json.dumps(
            {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "duration": ref_data.get("duration"),
                "view_count": ref_data.get("view_count"),
            }
        )

        values = {
            "source_id": source_id,
            "source_type": "youtube",
            "external_id": external_id,
            "title": ref_data.get("title"),
            "url": video_url,
            "published_at": published_at,
            "meta": meta,
            "content_id": content_id,
        }
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
