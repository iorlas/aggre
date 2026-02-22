"""Telegram public channel collector using Telethon (MTProto user-client)."""

from __future__ import annotations

import asyncio
import json

import sqlalchemy as sa
import structlog
from telethon import TelegramClient
from telethon.sessions import StringSession

from aggre.collectors.base import BaseCollector
from aggre.collectors.telegram.config import TelegramConfig, TelegramSource
from aggre.settings import Settings

# Columns to update on re-insert (views/forwards change over time)
_UPSERT_COLS = ("title", "content_text", "score", "meta")


class TelegramCollector(BaseCollector):
    """Collect messages from public Telegram channels."""

    source_type = "telegram"

    def collect(self, engine: sa.engine.Engine, config: TelegramConfig, settings: Settings, log: structlog.stdlib.BoundLogger) -> int:
        if not config.sources:
            return 0

        if not settings.telegram_api_id or not settings.telegram_session:
            log.warning("telegram.not_configured")
            return 0

        return asyncio.run(self._collect_async(engine, config, settings, log))

    async def _collect_async(
        self,
        engine: sa.engine.Engine,
        config: TelegramConfig,
        settings: Settings,
        log: structlog.stdlib.BoundLogger,
    ) -> int:
        client = TelegramClient(
            StringSession(settings.telegram_session),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        await client.connect()

        total_new = 0
        try:
            for tg_source in config.sources:
                log.info("telegram.collecting", username=tg_source.username)
                source_id = self._ensure_source(engine, tg_source.name)

                try:
                    count = await self._collect_channel(client, engine, source_id, tg_source, config, settings, log)
                    total_new += count
                except Exception:
                    log.exception("telegram.channel_error", username=tg_source.username)

                await asyncio.sleep(settings.telegram_rate_limit)
                self._update_last_fetched(engine, source_id)
        finally:
            await client.disconnect()

        return total_new

    async def _collect_channel(
        self,
        client: TelegramClient,
        engine: sa.engine.Engine,
        source_id: int,
        tg_source: TelegramSource,
        config: TelegramConfig,
        settings: Settings,
        log: structlog.stdlib.BoundLogger,
    ) -> int:
        messages = await client.get_messages(tg_source.username, limit=config.fetch_limit)

        new_count = 0
        with engine.begin() as conn:
            for msg in messages:
                text = msg.text
                if not text:
                    continue

                external_id = f"{tg_source.username}:{msg.id}"
                title = text.split("\n", 1)[0][:200]
                url = f"https://t.me/{tg_source.username}/{msg.id}"

                meta_dict = {}
                forwards = getattr(msg, "forwards", None)
                if forwards:
                    meta_dict["forwards"] = forwards
                media = getattr(msg, "media", None)
                if media:
                    meta_dict["media_type"] = type(media).__name__

                raw_data = {
                    "id": msg.id,
                    "text": text,
                    "date": msg.date.isoformat() if msg.date else None,
                    "views": getattr(msg, "views", None),
                    "forwards": forwards,
                }

                self._write_bronze(external_id, raw_data)

                values = dict(
                    source_id=source_id,
                    source_type="telegram",
                    external_id=external_id,
                    title=title,
                    content_text=text,
                    url=url,
                    author=tg_source.name,
                    published_at=msg.date.isoformat() if msg.date else None,
                    score=getattr(msg, "views", None) or 0,
                    comment_count=0,
                    meta=json.dumps(meta_dict) if meta_dict else None,
                )
                discussion_id = self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
                if discussion_id is not None:
                    new_count += 1

        log.info("telegram.discussions_stored", username=tg_source.username, new=new_count, total_seen=len(messages))
        return new_count
