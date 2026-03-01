"""Tests for the Telegram channel collector."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from aggre.collectors.telegram.collector import TelegramCollector
from aggre.collectors.telegram.config import TelegramConfig, TelegramSource
from aggre.config import AppConfig
from aggre.db import SilverObservation
from aggre.settings import Settings
from tests.factories import make_config, telegram_message, telegram_mock_client
from tests.helpers import collect, get_observations, get_sources

pytestmark = pytest.mark.integration


class TestTelegramCollectorDiscussions:
    def test_stores_messages(self, engine):
        config = make_config(
            telegram=TelegramConfig(sources=[TelegramSource(username="testchannel", name="Test Channel")]),
            telegram_api_id=12345,
            telegram_api_hash="abcdef",
            telegram_session="valid_session",
        )
        collector = TelegramCollector()

        msg = telegram_message(msg_id=42, text="First line\nSecond line", views=500, forwards=10)

        with (
            patch("aggre.collectors.telegram.collector.StringSession"),
            patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls,
        ):
            mock_cls.return_value = telegram_mock_client({"testchannel": [msg]})
            count = collect(collector, engine, config.telegram, config.settings)

        assert count == 1

        items = get_observations(engine)
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
        config = make_config(
            telegram=TelegramConfig(sources=[TelegramSource(username="testchannel", name="Test Channel")]),
            telegram_api_id=12345,
            telegram_api_hash="abcdef",
            telegram_session="valid_session",
        )
        collector = TelegramCollector()

        msg = telegram_message(msg_id=1)

        with (
            patch("aggre.collectors.telegram.collector.StringSession"),
            patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls,
        ):
            mock_cls.return_value = telegram_mock_client({"testchannel": [msg]})
            count1 = collect(collector, engine, config.telegram, config.settings)
            count2 = collect(collector, engine, config.telegram, config.settings)

        assert count1 == 1
        assert count2 == 1  # collect_references returns all API items; dedup is in upsert

    def test_multiple_channels(self, engine):
        channels = [
            TelegramSource(username="chan1", name="Channel 1"),
            TelegramSource(username="chan2", name="Channel 2"),
        ]
        config = make_config(
            telegram=TelegramConfig(sources=channels),
            telegram_api_id=12345,
            telegram_api_hash="abcdef",
            telegram_session="valid_session",
        )
        collector = TelegramCollector()

        messages = {
            "chan1": [telegram_message(msg_id=1, text="From chan1")],
            "chan2": [telegram_message(msg_id=2, text="From chan2")],
        }

        with (
            patch("aggre.collectors.telegram.collector.StringSession"),
            patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls,
        ):
            mock_cls.return_value = telegram_mock_client(messages)
            count = collect(collector, engine, config.telegram, config.settings)

        assert count == 2

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverObservation).order_by(SilverObservation.external_id)).fetchall()
            assert len(items) == 2
            assert items[0].external_id == "chan1:1"
            assert items[1].external_id == "chan2:2"

    def test_skips_empty_messages(self, engine):
        config = make_config(
            telegram=TelegramConfig(sources=[TelegramSource(username="testchannel", name="Test Channel")]),
            telegram_api_id=12345,
            telegram_api_hash="abcdef",
            telegram_session="valid_session",
        )
        collector = TelegramCollector()

        msg_empty = telegram_message(msg_id=1, text=None)
        msg_good = telegram_message(msg_id=2, text="Has text")

        with (
            patch("aggre.collectors.telegram.collector.StringSession"),
            patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls,
        ):
            mock_cls.return_value = telegram_mock_client({"testchannel": [msg_empty, msg_good]})
            count = collect(collector, engine, config.telegram, config.settings)

        assert count == 1

        items = get_observations(engine)
        assert len(items) == 1
        assert items[0].external_id == "testchannel:2"

    def test_no_config_returns_zero(self, engine):
        config = AppConfig(telegram=TelegramConfig(sources=[]), settings=Settings())
        collector = TelegramCollector()
        assert collect(collector, engine, config.telegram, config.settings) == 0

    def test_not_configured_returns_zero(self, engine):
        config = AppConfig(
            telegram=TelegramConfig(sources=[TelegramSource(username="test", name="Test")]),
            settings=Settings(telegram_api_id=0, telegram_session=""),
        )
        collector = TelegramCollector()
        assert collect(collector, engine, config.telegram, config.settings) == 0

    def test_updates_score_on_rerun(self, engine):
        config = make_config(
            telegram=TelegramConfig(sources=[TelegramSource(username="testchannel", name="Test Channel")]),
            telegram_api_id=12345,
            telegram_api_hash="abcdef",
            telegram_session="valid_session",
        )
        collector = TelegramCollector()

        msg_v1 = telegram_message(msg_id=1, text="Post", views=100, forwards=5)

        with (
            patch("aggre.collectors.telegram.collector.StringSession"),
            patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls,
        ):
            mock_cls.return_value = telegram_mock_client({"testchannel": [msg_v1]})
            collect(collector, engine, config.telegram, config.settings)

        # Second run with updated views
        msg_v2 = telegram_message(msg_id=1, text="Post", views=999, forwards=50)

        with (
            patch("aggre.collectors.telegram.collector.StringSession"),
            patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls,
        ):
            mock_cls.return_value = telegram_mock_client({"testchannel": [msg_v2]})
            count = collect(collector, engine, config.telegram, config.settings)

        # collect_references returns all API items; dedup + score update is in upsert
        assert count == 1

        items = get_observations(engine)
        assert len(items) == 1
        assert items[0].score == 999

        meta = json.loads(items[0].meta)
        assert meta["forwards"] == 50


class TestTelegramSource:
    def test_creates_source_row(self, engine):
        config = make_config(
            telegram=TelegramConfig(sources=[TelegramSource(username="testchannel", name="Test Channel")]),
            telegram_api_id=12345,
            telegram_api_hash="abcdef",
            telegram_session="valid_session",
        )
        collector = TelegramCollector()

        with (
            patch("aggre.collectors.telegram.collector.StringSession"),
            patch("aggre.collectors.telegram.collector.TelegramClient") as mock_cls,
        ):
            mock_cls.return_value = telegram_mock_client({"testchannel": []})
            collect(collector, engine, config.telegram, config.settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "telegram"
        assert rows[0].name == "Test Channel"
