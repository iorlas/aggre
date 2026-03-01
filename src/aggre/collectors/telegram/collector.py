"""Telegram public channel collector using Telethon (MTProto user-client)."""

from __future__ import annotations

import asyncio
import json
import logging

import sqlalchemy as sa
from telethon import TelegramClient
from telethon.sessions import StringSession

from aggre.collectors.base import BaseCollector, DiscussionRef
from aggre.collectors.telegram.config import TelegramConfig, TelegramSource
from aggre.settings import Settings

logger = logging.getLogger(__name__)

# Columns to update on re-insert (views/forwards change over time)
_UPSERT_COLS = ("title", "content_text", "score", "meta")


class TelegramCollector(BaseCollector):
    """Collect messages from public Telegram channels."""

    source_type = "telegram"

    def collect_discussions(self, engine: sa.engine.Engine, config: TelegramConfig, settings: Settings) -> list[DiscussionRef]:
        """Fetch Telegram messages, write bronze, return references."""
        if not config.sources:
            return []

        if not settings.telegram_api_id or not settings.telegram_session:
            logger.warning("telegram.not_configured")
            return []

        return asyncio.run(self._collect_refs_async(engine, config, settings))

    async def _collect_refs_async(
        self,
        engine: sa.engine.Engine,
        config: TelegramConfig,
        settings: Settings,
    ) -> list[DiscussionRef]:
        client = TelegramClient(
            StringSession(settings.telegram_session),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        await client.connect()

        refs: list[DiscussionRef] = []
        try:
            for tg_source in config.sources:
                logger.info("telegram.collecting username=%s", tg_source.username)
                source_id = self._ensure_source(engine, tg_source.name)

                try:
                    source_refs = await self._collect_channel_refs(client, source_id, tg_source, config)
                    refs.extend(source_refs)
                except Exception:
                    logger.exception("telegram.channel_error username=%s", tg_source.username)

                await asyncio.sleep(settings.telegram_rate_limit)
                self._update_last_fetched(engine, source_id)
        finally:
            await client.disconnect()

        return refs

    async def _collect_channel_refs(
        self,
        client: TelegramClient,
        source_id: int,
        tg_source: TelegramSource,
        config: TelegramConfig,
    ) -> list[DiscussionRef]:
        messages = await client.get_messages(tg_source.username, limit=config.fetch_limit)

        refs: list[DiscussionRef] = []
        for msg in messages:
            text = msg.text
            if not text:
                continue

            external_id = f"{tg_source.username}:{msg.id}"

            raw_data: dict[str, object] = {
                "id": msg.id,
                "text": text,
                "date": msg.date.isoformat() if msg.date else None,
                "views": getattr(msg, "views", None),
                "forwards": getattr(msg, "forwards", None),
                "media_type": type(getattr(msg, "media", None)).__name__ if getattr(msg, "media", None) else None,
                "_username": tg_source.username,
                "_source_name": tg_source.name,
            }

            self._write_bronze(external_id, raw_data)
            refs.append(DiscussionRef(external_id=external_id, raw_data=raw_data, source_id=source_id))

        logger.info("telegram.references_collected username=%s count=%d total_seen=%d", tg_source.username, len(refs), len(messages))
        return refs

    def process_discussion(
        self,
        ref_data: dict[str, object],
        conn: sa.Connection,
        source_id: int,
    ) -> None:
        """Normalize one Telegram message into silver rows."""
        text = ref_data.get("text", "")
        if not text:
            return

        username = ref_data.get("_username", "")
        source_name = ref_data.get("_source_name", "")
        msg_id = ref_data.get("id")
        external_id = f"{username}:{msg_id}"

        title = str(text).split("\n", 1)[0][:200]
        url = f"https://t.me/{username}/{msg_id}"

        meta_dict: dict[str, object] = {}
        forwards = ref_data.get("forwards")
        if forwards:
            meta_dict["forwards"] = forwards
        media_type = ref_data.get("media_type")
        if media_type:
            meta_dict["media_type"] = media_type

        values = dict(
            source_id=source_id,
            source_type="telegram",
            external_id=external_id,
            title=title,
            content_text=text,
            url=url,
            author=source_name,
            published_at=ref_data.get("date"),
            score=ref_data.get("views") or 0,
            comment_count=0,
            meta=json.dumps(meta_dict) if meta_dict else None,
        )
        self._upsert_discussion(conn, values, update_columns=_UPSERT_COLS)
