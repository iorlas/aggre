"""YouTube metadata collector using yt-dlp."""

from __future__ import annotations

import json

import sqlalchemy as sa
import structlog
import yt_dlp

from aggre.config import AppConfig
from aggre.db import content_items, raw_items, sources


class YoutubeCollector:
    """Fetches YouTube channel video metadata and stores entries in the database."""

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
                "fetching_youtube",
                name=yt_source.name,
                channel_id=yt_source.channel_id,
            )

            with engine.begin() as conn:
                row = conn.execute(
                    sa.select(sources.c.id).where(
                        sources.c.type == "youtube",
                        sources.c.name == yt_source.name,
                    )
                ).fetchone()

                if row is None:
                    result = conn.execute(
                        sa.insert(sources).values(
                            type="youtube",
                            name=yt_source.name,
                            config=json.dumps({"channel_id": yt_source.channel_id}),
                        )
                    )
                    source_id = result.inserted_primary_key[0]
                else:
                    source_id = row[0]

            url = f"https://www.youtube.com/channel/{yt_source.channel_id}/videos"
            ydl_opts = {
                "extract_flat": "in_playlist",
                "quiet": True,
                "no_warnings": True,
                "playlistend": None if backfill else config.settings.fetch_limit,
            }

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    entries = info.get("entries", []) if info else []
            except Exception:
                log.exception("youtube_fetch_error", channel=yt_source.name)
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
                    result = conn.execute(
                        sa.insert(raw_items)
                        .prefix_with("OR IGNORE")
                        .values(
                            source_type="youtube",
                            external_id=external_id,
                            raw_data=raw_data,
                        )
                    )

                    if result.rowcount == 0:
                        continue

                    raw_item_id = result.inserted_primary_key[0]

                    video_url = entry.get("url") or f"https://www.youtube.com/watch?v={external_id}"

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

                    conn.execute(
                        sa.insert(content_items)
                        .prefix_with("OR IGNORE")
                        .values(
                            source_id=source_id,
                            raw_item_id=raw_item_id,
                            source_type="youtube",
                            external_id=external_id,
                            title=entry.get("title"),
                            url=video_url,
                            published_at=published_at,
                            metadata=meta,
                            transcription_status="pending",
                        )
                    )

                    new_count += 1

            with engine.begin() as conn:
                conn.execute(sa.update(sources).where(sources.c.id == source_id).values(last_fetched_at=sa.text("datetime('now')")))

            log.info(
                "youtube_fetch_complete",
                name=yt_source.name,
                new_items=new_count,
            )
            total_new += new_count

        return total_new
