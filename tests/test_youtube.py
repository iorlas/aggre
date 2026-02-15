"""Tests for the YouTube collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
import structlog

from aggre.collectors.youtube import YoutubeCollector
from aggre.config import AppConfig, Settings, YoutubeSource
from aggre.db import BronzeDiscussion, SilverContent, SilverDiscussion, Source


def _make_config(fetch_limit: int = 10) -> AppConfig:
    return AppConfig(
        youtube=[
            YoutubeSource(channel_id="UC_test123", name="Test Channel"),
        ],
        settings=Settings(fetch_limit=fetch_limit),
    )


FAKE_ENTRIES = [
    {
        "id": "vid001",
        "title": "First Video",
        "url": "https://www.youtube.com/watch?v=vid001",
        "upload_date": "20240115",
        "duration": 600,
        "view_count": 1000,
    },
    {
        "id": "vid002",
        "title": "Second Video",
        "url": "https://www.youtube.com/watch?v=vid002",
        "upload_date": "20240120",
        "duration": 300,
        "view_count": 500,
    },
]


def _mock_extract_info(url, download=False):
    return {"entries": FAKE_ENTRIES}


class TestYoutubeCollector:
    def test_collect_inserts_new_items(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collector.collect(engine, config, log)

        assert count == 2

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(rows) == 2

            item1 = rows[0]
            assert item1.external_id == "vid001"
            assert item1.title == "First Video"
            assert item1.source_type == "youtube"
            assert item1.published_at == "2024-01-15"

            item2 = rows[1]
            assert item2.external_id == "vid002"
            assert item2.title == "Second Video"

            # Check meta JSON
            meta = json.loads(item1.meta)
            assert meta["channel_id"] == "UC_test123"
            assert meta["channel_name"] == "Test Channel"
            assert meta["duration"] == 600
            assert meta["view_count"] == 1000

            # transcription_status is now on SilverContent
            sc_rows = conn.execute(
                sa.select(SilverContent)
                .where(SilverContent.transcription_status == "pending")
            ).fetchall()
            assert len(sc_rows) == 2

    def test_collect_creates_source_row(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "youtube"
            assert rows[0].name == "Test Channel"
            src_config = json.loads(rows[0].config)
            assert src_config["channel_id"] == "UC_test123"

    def test_collect_stores_raw_items(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(BronzeDiscussion)).fetchall()
            assert len(rows) == 2
            assert rows[0].source_type == "youtube"
            raw = json.loads(rows[0].raw_data)
            assert raw["id"] == "vid001"

    def test_dedup_does_not_insert_duplicates(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count1 = collector.collect(engine, config, log)
            count2 = collector.collect(engine, config, log)

        assert count1 == 2
        assert count2 == 0

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(rows) == 2

    def test_collect_reuses_existing_source(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config, log)
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1

    def test_collect_sets_fetch_limit(self, engine):
        config = _make_config(fetch_limit=25)
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collector.collect(engine, config, log)

        opts = mock_cls.call_args[0][0]
        assert opts["playlistend"] == 25

    def test_collect_backfill_no_limit(self, engine):
        config = _make_config(fetch_limit=25)
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collector.collect(engine, config, log, backfill=True)

        opts = mock_cls.call_args[0][0]
        assert opts["playlistend"] is None

    def test_collect_skips_entries_without_id(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        entries_with_bad = [{"title": "No ID"}, *FAKE_ENTRIES]

        mock_ydl = MagicMock()
        mock_ydl.extract_info = lambda url, download=False: {"entries": entries_with_bad}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collector.collect(engine, config, log)

        assert count == 2

    def test_collect_handles_yt_dlp_error(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = Exception("Network error")
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collector.collect(engine, config, log)

        assert count == 0

    def test_collect_url_fallback(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        entry_no_url = [
            {
                "id": "vid_nourl",
                "title": "No URL Video",
                "upload_date": "20240101",
            },
        ]

        mock_ydl = MagicMock()
        mock_ydl.extract_info = lambda url, download=False: {"entries": entry_no_url}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert row.url == "https://www.youtube.com/watch?v=vid_nourl"
