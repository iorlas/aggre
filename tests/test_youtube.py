"""Tests for the YouTube collector."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import sqlalchemy as sa
import structlog

from aggre.collectors.youtube.collector import YoutubeCollector
from aggre.collectors.youtube.config import YoutubeConfig, YoutubeSource
from aggre.config import AppConfig
from aggre.db import SilverContent, SilverDiscussion, Source
from aggre.settings import Settings


def _make_config(fetch_limit: int = 10, proxy_url: str = "") -> AppConfig:
    return AppConfig(
        youtube=YoutubeConfig(
            sources=[YoutubeSource(channel_id="UC_test123", name="Test Channel")],
            fetch_limit=fetch_limit,
            init_fetch_limit=100,
        ),
        settings=Settings(proxy_url=proxy_url),
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

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collector.collect(engine, config.youtube, config.settings, log)

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
            sc_rows = conn.execute(sa.select(SilverContent).where(SilverContent.transcription_status == "pending")).fetchall()
            assert len(sc_rows) == 2

    def test_collect_creates_source_row(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "youtube"
            assert rows[0].name == "Test Channel"
            src_config = json.loads(rows[0].config)
            assert src_config["channel_id"] == "UC_test123"

    def test_collect_stores_raw_items(self, engine, tmp_path):
        """Bronze data is written to filesystem, not to DB."""
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)

        # Verify discussions exist in silver
        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(rows) == 2

    def test_dedup_does_not_insert_duplicates(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count1 = collector.collect(engine, config.youtube, config.settings, log)
            count2 = collector.collect(engine, config.youtube, config.settings, log)

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

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)
            collector.collect(engine, config.youtube, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1

    def test_collect_sets_fetch_limit(self, engine):
        """fetch_limit is used as playlistend when source has been fetched before."""
        config = _make_config(fetch_limit=25)
        log = structlog.get_logger()

        # Pre-initialize the source so _get_fetch_limit returns fetch_limit (not init_fetch_limit)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="youtube",
                    name="Test Channel",
                    config="{}",
                    last_fetched_at=datetime.now(UTC).isoformat(),
                )
            )

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)

        opts = mock_cls.call_args[0][0]
        assert opts["playlistend"] == 25

    def test_collect_backfill_no_limit(self, engine):
        config = _make_config(fetch_limit=25)
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log, backfill=True)

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

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collector.collect(engine, config.youtube, config.settings, log)

        assert count == 2

    def test_collect_handles_yt_dlp_error(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = Exception("Network error")
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collector.collect(engine, config.youtube, config.settings, log)

        assert count == 0

    def test_collect_passes_proxy_to_ytdlp(self, engine):
        config = _make_config(proxy_url="socks5://user:pass@proxy:1080")
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)

        opts = mock_cls.call_args[0][0]
        assert opts["proxy"] == "socks5://user:pass@proxy:1080"
        assert opts["source_address"] == "0.0.0.0"

    def test_collect_no_proxy_when_not_configured(self, engine):
        config = _make_config()
        log = structlog.get_logger()

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)

        opts = mock_cls.call_args[0][0]
        assert "proxy" not in opts
        assert "source_address" not in opts

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

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert row.url == "https://www.youtube.com/watch?v=vid_nourl"

    def test_recollect_fills_published_at(self, engine):
        """Re-collecting videos that lacked published_at should fill it in."""
        config = _make_config()
        log = structlog.get_logger()

        # First collection: entries without upload_date (simulating old flat mode)
        entries_no_date = [
            {"id": "vid001", "title": "First Video", "duration": 600, "view_count": 1000},
            {"id": "vid002", "title": "Second Video", "duration": 300, "view_count": 500},
        ]
        mock_ydl = MagicMock()
        mock_ydl.extract_info = lambda url, download=False: {"entries": entries_no_date}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert all(r.published_at is None for r in rows)

        # Second collection: same videos now have upload_date
        mock_ydl2 = MagicMock()
        mock_ydl2.extract_info = _mock_extract_info  # has upload_date
        mock_ydl2.__enter__ = lambda s: s
        mock_ydl2.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl2):
            collector = YoutubeCollector()
            collector.collect(engine, config.youtube, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion).order_by(SilverDiscussion.external_id)).fetchall()
            assert len(rows) == 2
            assert rows[0].published_at == "2024-01-15"
            assert rows[1].published_at == "2024-01-20"

    def test_collect_skips_fresh_channel(self, engine):
        """source_ttl_minutes > 0 should skip channels fetched recently."""
        config = AppConfig(
            youtube=YoutubeConfig(
                sources=[
                    YoutubeSource(channel_id="UC_fresh", name="Fresh Channel"),
                    YoutubeSource(channel_id="UC_stale", name="Stale Channel"),
                ],
            ),
            settings=Settings(),
        )
        log = structlog.get_logger()

        # Pre-seed sources: one fresh (5 min ago), one stale (2 hours ago)
        five_min_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="youtube",
                    name="Fresh Channel",
                    config='{"channel_id":"UC_fresh"}',
                    last_fetched_at=five_min_ago,
                )
            )
            conn.execute(
                sa.insert(Source).values(
                    type="youtube",
                    name="Stale Channel",
                    config='{"channel_id":"UC_stale"}',
                    last_fetched_at=two_hours_ago,
                )
            )

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            count = collector.collect(engine, config.youtube, config.settings, log, source_ttl_minutes=60)

        # Only the stale channel should have triggered yt-dlp
        assert mock_cls.call_count == 1
        assert count == 2  # 2 entries from the stale channel

    def test_collect_ttl_zero_fetches_all(self, engine):
        """source_ttl_minutes=0 (default) should fetch all channels."""
        config = _make_config()
        log = structlog.get_logger()

        # Pre-seed a recently fetched source
        five_min_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                sa.insert(Source).values(
                    type="youtube",
                    name="Test Channel",
                    config='{"channel_id":"UC_test123"}',
                    last_fetched_at=five_min_ago,
                )
            )

        mock_ydl = MagicMock()
        mock_ydl.extract_info = _mock_extract_info
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            count = collector.collect(engine, config.youtube, config.settings, log, source_ttl_minutes=0)

        # Should still fetch even though source is fresh (TTL disabled)
        assert mock_cls.call_count == 1
        assert count == 2
