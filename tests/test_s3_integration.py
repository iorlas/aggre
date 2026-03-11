"""Integration tests proving high-level functions work with S3 (moto) backend.

These catch the exact classes of bugs that went undetected when tests only used
filesystem fixtures (tmp_path / tmp_bronze):
  - C1: reprocess used Path.glob on S3Store (now uses list_keys)
  - C2: transcription audio not uploaded to S3 after download
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.db import SilverContent, SilverDiscussion
from aggre.utils.bronze import S3Store
from tests.factories import hn_hit, make_config, seed_content, seed_discussion

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def s3_store():
    """Moto-backed S3Store — no real AWS calls."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bronze")

        store = S3Store.__new__(S3Store)
        store._bucket = "test-bronze"
        store._s3 = client
        yield store


@pytest.fixture()
def s3_backend(s3_store, monkeypatch):
    """Inject S3Store as the global bronze store.

    After this, ``get_store()`` and ``_store_for(DEFAULT_BRONZE_ROOT)`` both
    return the moto-backed S3Store.
    """
    import aggre.utils.bronze as bronze_mod

    monkeypatch.setattr(bronze_mod, "_store", s3_store)
    yield s3_store


# ---------------------------------------------------------------------------
# Reprocess
# ---------------------------------------------------------------------------


class TestReprocessViaS3:
    def test_reprocess_reads_from_s3(self, engine, s3_backend):
        """Write raw.json to S3, reprocess without bronze_root override — uses S3."""
        from aggre.workflows.reprocess import reprocess_from_bronze

        raw_data = hn_hit(object_id="12345", title="S3 Story", url="https://example.com/s3")
        s3_backend.write("hackernews/12345/raw.json", json.dumps(raw_data))

        count = reprocess_from_bronze(engine)
        assert count == 1

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(rows) == 1
            assert rows[0].source_type == "hackernews"
            assert rows[0].external_id == "12345"
            assert rows[0].title == "S3 Story"

    def test_reprocess_list_keys_on_s3(self, engine, s3_backend):
        """Verify list_keys finds multiple raw.json files on S3."""
        from aggre.workflows.reprocess import reprocess_from_bronze

        for eid in ("aaa", "bbb"):
            raw = hn_hit(object_id=eid, title=f"Story {eid}")
            s3_backend.write(f"hackernews/{eid}/raw.json", json.dumps(raw))

        count = reprocess_from_bronze(engine)
        assert count == 2


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


def _seed_youtube(engine, external_id="abc123", title="Test Video", meta=None):
    """Seed SilverContent + SilverDiscussion for a YouTube video."""
    content_id = seed_content(
        engine,
        f"https://youtube.com/watch?v={external_id}",
        domain="youtube.com",
    )
    seed_discussion(
        engine,
        source_type="youtube",
        external_id=external_id,
        content_id=content_id,
        title=title,
        meta=meta,
    )
    return content_id


def _mock_transcribe(transcript_text="This is the transcript", language="en"):
    from aggre.utils.whisper_client import TranscriptionResult

    return TranscriptionResult(text=transcript_text, language=language, server_name="test-whisper")


class TestTranscriptionViaS3:
    def test_whisper_cache_from_s3(self, engine, s3_backend):
        """When whisper.json exists in S3, transcription uses it without download."""
        from aggre.workflows.transcription import transcribe_one

        content_id = _seed_youtube(engine, external_id="cached01")
        config = make_config()

        whisper_data = json.dumps({"transcript": "S3 cached transcript", "language": "fr"})
        s3_backend.write("youtube/cached01/whisper.json", whisper_data)

        result = transcribe_one(engine, config, content_id)
        assert result == "cached"

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent).where(SilverContent.id == content_id)).fetchone()
            assert row.text == "S3 cached transcript"
            assert row.detected_language == "fr"

    @patch("aggre.workflows.transcription.transcribe_audio")
    @patch("aggre.workflows.transcription.yt_dlp.YoutubeDL")
    def test_audio_uploaded_to_s3_after_download(self, mock_ydl_cls, mock_transcribe_audio, engine, s3_backend, tmp_path):
        """After downloading audio, it's uploaded to S3 for persistence."""
        from aggre.workflows.transcription import transcribe_one

        content_id = _seed_youtube(engine, external_id="up01")
        config = make_config(proxy_url="")
        # Point youtube_temp_dir to a real tmp directory
        config.settings.youtube_temp_dir = str(tmp_path / "videos")
        mock_transcribe_audio.return_value = _mock_transcribe()

        # Mock yt_dlp to create a fake audio file matching yt_dlp's output pattern
        # (the code globs for {external_id}.* then renames to audio.opus)
        def fake_download(urls):
            dest = tmp_path / "videos" / "up01"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "up01.opus").write_bytes(b"fake opus audio")

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.download.side_effect = fake_download
        mock_ydl_cls.return_value.__enter__ = MagicMock(return_value=mock_ydl_instance)
        mock_ydl_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = transcribe_one(engine, config, content_id)
        assert result == "transcribed"

        # Audio should have been uploaded to S3
        audio_bytes = s3_backend.read_bytes("youtube/up01/audio.opus")
        assert audio_bytes == b"fake opus audio"

    @patch("aggre.workflows.transcription.transcribe_audio")
    def test_audio_downloaded_from_s3_skips_yt_dlp(self, mock_transcribe_audio, engine, s3_backend, tmp_path):
        """When audio exists in S3, yt_dlp is NOT called."""
        from aggre.workflows.transcription import transcribe_one

        content_id = _seed_youtube(engine, external_id="s3aud01")
        config = make_config()
        config.settings.youtube_temp_dir = str(tmp_path / "videos")
        mock_transcribe_audio.return_value = _mock_transcribe(transcript_text="From S3 audio")

        # Pre-upload audio to S3
        s3_backend.write_bytes("youtube/s3aud01/audio.opus", b"s3 cached audio")

        with patch("aggre.workflows.transcription.yt_dlp.YoutubeDL") as mock_ydl_cls:
            result = transcribe_one(engine, config, content_id)

        assert result == "transcribed"
        # yt_dlp should never have been instantiated
        mock_ydl_cls.assert_not_called()

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent).where(SilverContent.text.isnot(None))).fetchone()
            assert row.text == "From S3 audio"


# ---------------------------------------------------------------------------
# bronze_http
# ---------------------------------------------------------------------------


class TestS3StorePagination:
    def test_paginated_listing(self):
        """list_keys handles IsTruncated=True with continuation tokens."""
        store = S3Store.__new__(S3Store)
        store._bucket = "test"
        mock_s3 = MagicMock()
        store._s3 = mock_s3

        mock_s3.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": "a/1.json"}, {"Key": "a/2.json"}],
                "IsTruncated": True,
                "NextContinuationToken": "token123",
            },
            {
                "Contents": [{"Key": "a/3.json"}],
                "IsTruncated": False,
            },
        ]

        keys = store.list_keys("a/")
        assert keys == ["a/1.json", "a/2.json", "a/3.json"]
        assert mock_s3.list_objects_v2.call_count == 2


class TestGetStore:
    def test_filesystem_backend_from_settings(self, monkeypatch):
        """get_store() initializes FilesystemStore from settings."""
        import aggre.utils.bronze as bronze_mod
        from aggre.utils.bronze import FilesystemStore

        bronze_mod._reset_store()

        monkeypatch.setenv("AGGRE_BRONZE_BACKEND", "filesystem")
        monkeypatch.setenv("AGGRE_BRONZE_ROOT", "/tmp/test-bronze-store")

        store = bronze_mod.get_store()
        assert isinstance(store, FilesystemStore)

        bronze_mod._reset_store()

    def test_reset_store_clears_singleton(self, monkeypatch):
        """_reset_store() forces re-initialization on next get_store() call."""
        import aggre.utils.bronze as bronze_mod
        from aggre.utils.bronze import FilesystemStore

        bronze_mod._reset_store()

        monkeypatch.setenv("AGGRE_BRONZE_BACKEND", "filesystem")
        monkeypatch.setenv("AGGRE_BRONZE_ROOT", "/tmp/bronze-a")
        store_a = bronze_mod.get_store()

        bronze_mod._reset_store()

        monkeypatch.setenv("AGGRE_BRONZE_ROOT", "/tmp/bronze-b")
        store_b = bronze_mod.get_store()

        assert store_a is not store_b
        assert isinstance(store_b, FilesystemStore)

        bronze_mod._reset_store()


class TestBronzeHttpViaS3:
    def test_cache_hit_from_s3(self, s3_backend):
        """Pre-populated S3 bronze — HTTP not called, returns cached data."""
        from aggre.utils.bronze_http import fetch_item_json

        data = {"title": "S3 Cached", "points": 42}
        s3_backend.write("hackernews/s3hit/raw.json", json.dumps(data))

        client = MagicMock()

        result = fetch_item_json(
            "hackernews",
            "s3hit",
            "https://hn.algolia.com/api/v1/items/s3hit",
            client,
        )

        assert result == data
        client.get.assert_not_called()

    def test_cache_miss_writes_to_s3(self, s3_backend):
        """Cache miss fetches from HTTP and writes to S3."""
        from aggre.utils.bronze_http import fetch_item_json

        data = {"title": "Fresh Fetch", "points": 99}
        resp = MagicMock()
        resp.json.return_value = data
        resp.raise_for_status.return_value = None
        client = MagicMock()
        client.get.return_value = resp

        result = fetch_item_json(
            "hackernews",
            "s3miss",
            "https://hn.algolia.com/api/v1/items/s3miss",
            client,
        )

        assert result == data
        client.get.assert_called_once()

        # Verify it was written to S3
        cached = json.loads(s3_backend.read("hackernews/s3miss/raw.json"))
        assert cached == data
