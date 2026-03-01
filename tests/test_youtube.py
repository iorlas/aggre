"""Tests for the YouTube collector."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.collectors.youtube.collector import YoutubeCollector
from aggre.collectors.youtube.config import YoutubeConfig, YoutubeSource
from aggre.db import SilverContent, SilverObservation, Source
from tests.factories import make_config, youtube_entry
from tests.helpers import collect, get_observations, get_sources

pytestmark = pytest.mark.integration


def _mock_ydl(entries: list[dict]) -> MagicMock:
    """Build a MagicMock that behaves like a yt_dlp.YoutubeDL context manager."""
    mock = MagicMock()
    mock.extract_info = lambda url, download=False: {"entries": entries}
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _default_config(**kwargs):
    return make_config(
        youtube=YoutubeConfig(
            sources=[YoutubeSource(channel_id="UC_test123", name="Test Channel")],
            fetch_limit=kwargs.pop("fetch_limit", 10),
            init_fetch_limit=100,
        ),
        **kwargs,
    )


def _default_entries() -> list[dict]:
    return [
        youtube_entry(video_id="vid001", title="First Video", upload_date="20240115", duration=600, view_count=1000),
        youtube_entry(video_id="vid002", title="Second Video", upload_date="20240120", duration=300, view_count=500),
    ]


class TestYoutubeCollector:
    def test_collect_inserts_new_items(self, engine, log):
        config = _default_config()
        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collect(collector, engine, config.youtube, config.settings, log)

        assert count == 2

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverObservation)).fetchall()
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

            # Content rows should be ready for transcription (text=NULL, error=NULL)
            sc_rows = conn.execute(sa.select(SilverContent).where(SilverContent.text.is_(None), SilverContent.error.is_(None))).fetchall()
            assert len(sc_rows) == 2

    def test_collect_creates_source_row(self, engine, log):
        config = _default_config()
        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "youtube"
        assert rows[0].name == "Test Channel"
        src_config = json.loads(rows[0].config)
        assert src_config["channel_id"] == "UC_test123"

    def test_collect_stores_raw_items(self, engine, log):
        """Bronze data is written to filesystem, not to DB."""
        config = _default_config()
        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)

        # Verify discussions exist in silver
        assert len(get_observations(engine)) == 2

    def test_dedup_does_not_insert_duplicates(self, engine, log):
        config = _default_config()
        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count1 = collect(collector, engine, config.youtube, config.settings, log)
            count2 = collect(collector, engine, config.youtube, config.settings, log)

        assert count1 == 2
        assert count2 == 2  # collect_references returns all API items; dedup is in upsert

        assert len(get_observations(engine)) == 2

    def test_collect_reuses_existing_source(self, engine, log):
        config = _default_config()
        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)
            collect(collector, engine, config.youtube, config.settings, log)

        assert len(get_sources(engine)) == 1

    def test_collect_sets_fetch_limit(self, engine, log):
        """fetch_limit is used as playlistend when source has been fetched before."""
        config = _default_config(fetch_limit=25)

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

        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)

        opts = mock_cls.call_args[0][0]
        assert opts["playlistend"] == 25

    def test_collect_backfill_no_limit(self, engine, log):
        config = _default_config(fetch_limit=25)
        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log, backfill=True)

        opts = mock_cls.call_args[0][0]
        assert opts["playlistend"] is None

    def test_collect_skips_entries_without_id(self, engine, log):
        config = _default_config()
        entries_with_bad = [{"title": "No ID"}, *_default_entries()]
        mock_ydl = _mock_ydl(entries_with_bad)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collect(collector, engine, config.youtube, config.settings, log)

        assert count == 2

    def test_collect_handles_yt_dlp_error(self, engine, log):
        config = _default_config()

        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = Exception("Network error")
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            count = collect(collector, engine, config.youtube, config.settings, log)

        assert count == 0

    def test_collect_passes_proxy_to_ytdlp(self, engine, log):
        config = _default_config(proxy_url="socks5://user:pass@proxy:1080")
        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)

        opts = mock_cls.call_args[0][0]
        assert opts["proxy"] == "socks5://user:pass@proxy:1080"
        assert opts["source_address"] == "0.0.0.0"

    def test_collect_no_proxy_when_not_configured(self, engine, log):
        config = _default_config()
        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)

        opts = mock_cls.call_args[0][0]
        assert "proxy" not in opts
        assert "source_address" not in opts

    def test_collect_url_fallback(self, engine, log):
        config = _default_config()

        entry_no_url = [
            {
                "id": "vid_nourl",
                "title": "No URL Video",
                "upload_date": "20240101",
            },
        ]
        mock_ydl = _mock_ydl(entry_no_url)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)

        rows = get_observations(engine)
        assert rows[0].url == "https://www.youtube.com/watch?v=vid_nourl"

    def test_recollect_fills_published_at(self, engine, log):
        """Re-collecting videos that lacked published_at should fill it in."""
        config = _default_config()

        # First collection: entries without upload_date (simulating old flat mode)
        entries_no_date = [
            {"id": "vid001", "title": "First Video", "duration": 600, "view_count": 1000},
            {"id": "vid002", "title": "Second Video", "duration": 300, "view_count": 500},
        ]
        mock_ydl = _mock_ydl(entries_no_date)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)

        rows = get_observations(engine)
        assert all(r.published_at is None for r in rows)

        # Second collection: same videos now have upload_date
        mock_ydl2 = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl2):
            collector = YoutubeCollector()
            collect(collector, engine, config.youtube, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverObservation).order_by(SilverObservation.external_id)).fetchall()
            assert len(rows) == 2
            assert rows[0].published_at == "2024-01-15"
            assert rows[1].published_at == "2024-01-20"

    def test_collect_skips_fresh_channel(self, engine, log):
        """source_ttl_minutes > 0 should skip channels fetched recently."""
        config = make_config(
            youtube=YoutubeConfig(
                sources=[
                    YoutubeSource(channel_id="UC_fresh", name="Fresh Channel"),
                    YoutubeSource(channel_id="UC_stale", name="Stale Channel"),
                ],
            ),
        )

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

        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            count = collect(collector, engine, config.youtube, config.settings, log, source_ttl_minutes=60)

        # Only the stale channel should have triggered yt-dlp
        assert mock_cls.call_count == 1
        assert count == 2  # 2 entries from the stale channel

    def test_collect_ttl_zero_fetches_all(self, engine, log):
        """source_ttl_minutes=0 (default) should fetch all channels."""
        config = _default_config()

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

        mock_ydl = _mock_ydl(_default_entries())

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            collector = YoutubeCollector()
            count = collect(collector, engine, config.youtube, config.settings, log, source_ttl_minutes=0)

        # Should still fetch even though source is fresh (TTL disabled)
        assert mock_cls.call_count == 1
        assert count == 2
