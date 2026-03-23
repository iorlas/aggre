from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

__all__ = ["telegram_message", "telegram_mock_client"]


def telegram_message(
    msg_id: int = 1,
    text: str | None = "Hello world",
    date: datetime | None = None,
    views: int = 100,
    forwards: int = 5,
    media: object | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.date = date or datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    msg.views = views
    msg.forwards = forwards
    msg.media = media
    return msg


def telegram_mock_client(messages_by_username: dict[str, list]) -> AsyncMock:
    """Build a mock TelegramClient that returns configured messages."""
    client = AsyncMock()

    async def get_messages(username: str, limit: int = 100):
        return messages_by_username.get(username, [])

    client.get_messages = AsyncMock(side_effect=get_messages)
    return client
