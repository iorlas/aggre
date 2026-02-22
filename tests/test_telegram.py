"""Tests for the Telegram channel collector."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import sqlalchemy as sa

from aggre.collectors.telegram import TelegramCollector
from aggre.config import AppConfig, TelegramConfig, TelegramSource
from aggre.settings import Settings
from aggre.db import BronzeDiscussion, SilverDiscussion, Source


def _make_config(channels: list[TelegramSource] | None = None) -> AppConfig:
    return AppConfig(
        telegram=TelegramConfig(
            sources=channels or [TelegramSource(username="testchannel", name="Test Channel")],
        ),
        settings=Settings(
            telegram_api_id=12345,
            telegram_api_hash="abcdef",
            telegram_session="valid_session",
            telegram_rate_limit=0,  # no delay in tests
        ),
    )


def _make_message(
    msg_id: int = 1,
    text: str = "Hello world",
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


def _mock_client(messages_by_username: dict[str, list]) -> AsyncMock:
    client = AsyncMock()

    async def get_messages(username, limit=100):
        return messages_by_username.get(username, [])

    client.get_messages = AsyncMock(side_effect=get_messages)
    return client


class TestTelegramCollectorDiscussions:
    def test_stores_messages(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = TelegramCollector()

        msg = _make_message(msg_id=42, text="First line\nSecond line", views=500, forwards=10)

        with patch("aggre.collectors.telegram.collector.StringSession"), \
             patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls:
            mock_cls.return_value = _mock_client({"testchannel": [msg]})
            count = collector.collect(engine, config.telegram, config.settings, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(BronzeDiscussion)).fetchall()
            assert len(raws) == 1
            assert raws[0].external_id == "testchannel:42"
            assert raws[0].source_type == "telegram"

            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].title == "First line"
            assert items[0].content_text == "First line\nSecond line"
            assert items[0].url == "https://t.me/testchannel/42"
            assert items[0].author == "Test Channel"
            assert items[0].source_type == "telegram"
            assert items[0].score == 500
            assert items[0].comment_count == 0

            meta = json.loads(items[0].meta)
            assert meta["forwards"] == 10

    def test_dedup_across_runs(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = TelegramCollector()

        msg = _make_message(msg_id=1)

        with patch("aggre.collectors.telegram.collector.StringSession"), \
             patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls:
            mock_cls.return_value = _mock_client({"testchannel": [msg]})
            count1 = collector.collect(engine, config.telegram, config.settings, log)
            count2 = collector.collect(engine, config.telegram, config.settings, log)

        assert count1 == 1
        assert count2 == 0

    def test_multiple_channels(self, engine):
        channels = [
            TelegramSource(username="chan1", name="Channel 1"),
            TelegramSource(username="chan2", name="Channel 2"),
        ]
        config = _make_config(channels)
        log = MagicMock()
        collector = TelegramCollector()

        messages = {
            "chan1": [_make_message(msg_id=1, text="From chan1")],
            "chan2": [_make_message(msg_id=2, text="From chan2")],
        }

        with patch("aggre.collectors.telegram.collector.StringSession"), \
             patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls:
            mock_cls.return_value = _mock_client(messages)
            count = collector.collect(engine, config.telegram, config.settings, log)

        assert count == 2

        with engine.connect() as conn:
            items = conn.execute(
                sa.select(SilverDiscussion).order_by(SilverDiscussion.external_id)
            ).fetchall()
            assert len(items) == 2
            assert items[0].external_id == "chan1:1"
            assert items[1].external_id == "chan2:2"

    def test_skips_empty_messages(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = TelegramCollector()

        msg_empty = _make_message(msg_id=1, text=None)
        msg_good = _make_message(msg_id=2, text="Has text")

        with patch("aggre.collectors.telegram.collector.StringSession"), \
             patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls:
            mock_cls.return_value = _mock_client({"testchannel": [msg_empty, msg_good]})
            count = collector.collect(engine, config.telegram, config.settings, log)

        assert count == 1

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].external_id == "testchannel:2"

    def test_no_config_returns_zero(self, engine):
        config = AppConfig(telegram=TelegramConfig(sources=[]), settings=Settings())
        log = MagicMock()
        collector = TelegramCollector()
        assert collector.collect(engine, config.telegram, config.settings, log) == 0

    def test_not_configured_returns_zero(self, engine):
        config = AppConfig(
            telegram=TelegramConfig(sources=[TelegramSource(username="test", name="Test")]),
            settings=Settings(telegram_api_id=0, telegram_session=""),
        )
        log = MagicMock()
        collector = TelegramCollector()
        assert collector.collect(engine, config.telegram, config.settings, log) == 0

    def test_updates_score_on_rerun(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = TelegramCollector()

        msg_v1 = _make_message(msg_id=1, text="Post", views=100, forwards=5)

        with patch("aggre.collectors.telegram.collector.StringSession"), \
             patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls:
            mock_cls.return_value = _mock_client({"testchannel": [msg_v1]})
            collector.collect(engine, config.telegram, config.settings, log)

        # Second run with updated views
        msg_v2 = _make_message(msg_id=1, text="Post", views=999, forwards=50)

        with patch("aggre.collectors.telegram.collector.StringSession"), \
             patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls:
            mock_cls.return_value = _mock_client({"testchannel": [msg_v2]})
            count = collector.collect(engine, config.telegram, config.settings, log)

        # Dedup returns 0 for new count, but score should be updated
        assert count == 0

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].score == 999

            meta = json.loads(items[0].meta)
            assert meta["forwards"] == 50


class TestTelegramSource:
    def test_creates_source_row(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = TelegramCollector()

        with patch("aggre.collectors.telegram.collector.StringSession"), \
             patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls:
            mock_cls.return_value = _mock_client({"testchannel": []})
            collector.collect(engine, config.telegram, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "telegram"
            assert rows[0].name == "Test Channel"
