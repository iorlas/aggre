"""YouTube metadata collector using yt-dlp."""

from __future__ import annotations

import json

import sqlalchemy as sa
import structlog
import yt_dlp

from aggre.collectors.base import BaseCollector
from aggre.config import AppConfig
from aggre.db import SilverContent
from aggre.statuses import TranscriptionStatus
from aggre.urls import ensure_content

# Columns to update on re-insert (titles always fresh)
_UPSERT_COLS = ("title", "url", "meta")


class YoutubeCollector(BaseCollector):
    """Fetches YouTube channel video metadata and stores entries in the database."""

    source_type = "youtube"

    def collect(
        self,
        engine: sa.engine.Engine,
        config: AppConfig,
        log: structlog.stdlib.BoundLogger,
        backfill: bool = False,
    ) -> int:
        total_new = 0

        for yt_source in config.youtube:
            log.info(
                "youtube.collecting",
                name=yt_source.name,
                channel_id=yt_source.channel_id,
            )

            source_id = self._ensure_source(engine, yt_source.name, {"channel_id": yt_source.channel_id})

            url = f"https://www.youtube.com/channel/{yt_source.channel_id}/videos"
            ydl_opts = {
                "extract_flat": "in_playlist",
                "quiet": True,
                "no_warnings": True,
                "playlistend": None if backfill else config.settings.fetch_limit,
            }
            if config.settings.proxy_url:
                ydl_opts["proxy"] = config.settings.proxy_url
                ydl_opts["source_address"] = "0.0.0.0"

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    entries = info.get("entries", []) if info else []
            except Exception:
                log.exception("youtube.fetch_error", channel=yt_source.name)
                continue

            new_count = 0

            for entry in entries:
                if not entry:
                    continue

                external_id = entry.get("id")
                if not external_id:
                    log.warning("skipping_entry_no_id", channel=yt_source.name)
                    continue

                raw_data = json.dumps(entry)

                with engine.begin() as conn:
                    raw_id = self._store_raw_item(conn, external_id, raw_data)

                    video_url = entry.get("url") or f"https://www.youtube.com/watch?v={external_id}"

                    # Create content entry for the video URL
                    content_id = ensure_content(conn, video_url)

                    # Set transcription_status on content (content-level concern)
                    if content_id:
                        conn.execute(
                            sa.update(SilverContent)
                            .where(SilverContent.id == content_id, SilverContent.transcription_status.is_(None))
                            .values(transcription_status=TranscriptionStatus.PENDING)
                        )

                    # Format upload_date from YYYYMMDD to YYYY-MM-DD if present
                    published_at = None
                    upload_date = entry.get("upload_date")
                    if upload_date and len(upload_date) == 8:
                        published_at = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
                    elif upload_date:
                        published_at = upload_date

                    meta = json.dumps(
                        {
                            "channel_id": yt_source.channel_id,
                            "channel_name": yt_source.name,
                            "duration": entry.get("duration"),
                            "view_count": entry.get("view_count"),
                        }
                    )

                    values = dict(
                        source_id=source_id,
                        bronze_discussion_id=raw_id,
                        source_type="youtube",
                        external_id=external_id,
                        title=entry.get("title"),
                        url=video_url,
                        published_at=published_at,
                        meta=meta,
                        content_id=content_id,
                    )
                    result = self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
                    if result is not None:
                        new_count += 1

            self._update_last_fetched(engine, source_id)

            log.info(
                "youtube.discussions_stored",
                name=yt_source.name,
                new_discussions=new_count,
            )
            total_new += new_count

        return total_new
