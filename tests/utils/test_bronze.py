"""Tests for bronze filesystem storage module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aggre.utils.bronze import (
    FilesystemStore,
    S3Store,
    bronze_exists,
    bronze_exists_by_url,
    bronze_path,
    read_bronze,
    read_bronze_by_url,
    read_bronze_json,
    url_hash,
    write_bronze,
    write_bronze_by_url,
    write_bronze_json,
)

pytestmark = pytest.mark.unit


class TestBronzePath:
    def test_returns_correct_path_structure(self, tmp_path: Path) -> None:
        result = bronze_path("hackernews", "12345", "raw", "json", bronze_root=tmp_path)
        assert result == tmp_path / "hackernews" / "12345" / "raw.json"

    def test_handles_nested_source_type(self, tmp_path: Path) -> None:
        result = bronze_path("youtube", "dQw4w9WgXcQ", "audio", "opus", bronze_root=tmp_path)
        assert result == tmp_path / "youtube" / "dQw4w9WgXcQ" / "audio.opus"


class TestWriteAndReadBronze:
    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        content = "<html><body>Hello</body></html>"
        write_bronze("hackernews", "12345", "raw", content, "html", bronze_root=tmp_path)
        result = read_bronze("hackernews", "12345", "raw", "html", bronze_root=tmp_path)
        assert result == content

    def test_creates_directories(self, tmp_path: Path) -> None:
        write_bronze("reddit", "abc123", "raw", "data", "json", bronze_root=tmp_path)
        assert (tmp_path / "reddit" / "abc123").is_dir()

    def test_returns_written_path(self, tmp_path: Path) -> None:
        result = write_bronze("rss", "feed1", "raw", "content", "xml", bronze_root=tmp_path)
        assert result == tmp_path / "rss" / "feed1" / "raw.xml"

    def test_atomic_write_no_tmp_file_lingers(self, tmp_path: Path) -> None:
        write_bronze("hackernews", "12345", "raw", "data", "json", bronze_root=tmp_path)
        parent = tmp_path / "hackernews" / "12345"
        tmp_files = list(parent.glob("*.tmp"))
        assert tmp_files == []

    def test_read_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_bronze("hackernews", "nonexistent", "raw", "json", bronze_root=tmp_path)


class TestWriteAndReadBronzeJson:
    def test_json_roundtrip(self, tmp_path: Path) -> None:
        data = {"title": "Test Story", "points": 100, "tags": ["python", "rust"]}
        write_bronze_json("hackernews", "12345", data, bronze_root=tmp_path)
        result = read_bronze_json("hackernews", "12345", "raw", bronze_root=tmp_path)
        assert result == data

    def test_preserves_unicode(self, tmp_path: Path) -> None:
        data = {"title": "Geist mit Umlauten: aou"}
        write_bronze_json("rss", "feed1", data, bronze_root=tmp_path)
        result = read_bronze_json("rss", "feed1", "raw", bronze_root=tmp_path)
        assert result == data

    def test_writes_to_raw_json(self, tmp_path: Path) -> None:
        write_bronze_json("hackernews", "99", {"key": "value"}, bronze_root=tmp_path)
        path = tmp_path / "hackernews" / "99" / "raw.json"
        assert path.exists()
        parsed = json.loads(path.read_text())
        assert parsed == {"key": "value"}


class TestBronzeExists:
    def test_returns_false_when_missing(self, tmp_path: Path) -> None:
        assert bronze_exists("hackernews", "12345", "raw", "json", bronze_root=tmp_path) is False

    def test_returns_true_after_write(self, tmp_path: Path) -> None:
        write_bronze("hackernews", "12345", "raw", "data", "json", bronze_root=tmp_path)
        assert bronze_exists("hackernews", "12345", "raw", "json", bronze_root=tmp_path) is True


class TestUrlHash:
    def test_produces_stable_hash(self) -> None:
        url = "https://example.com/article"
        assert url_hash(url) == url_hash(url)

    def test_hash_is_16_chars(self) -> None:
        result = url_hash("https://example.com/article")
        assert len(result) == 16

    def test_hash_is_hex(self) -> None:
        result = url_hash("https://example.com/article")
        int(result, 16)  # raises ValueError if not valid hex

    def test_different_urls_produce_different_hashes(self) -> None:
        hash1 = url_hash("https://example.com/page1")
        hash2 = url_hash("https://example.com/page2")
        assert hash1 != hash2


class TestBronzeByUrl:
    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        url = "https://example.com/article?id=42"
        content = "<html>Article content</html>"
        write_bronze_by_url("fetch", url, "response", content, "html", bronze_root=tmp_path)
        result = read_bronze_by_url("fetch", url, "response", "html", bronze_root=tmp_path)
        assert result == content

    def test_uses_url_hash_as_directory(self, tmp_path: Path) -> None:
        url = "https://example.com/article"
        write_bronze_by_url("fetch", url, "response", "data", "html", bronze_root=tmp_path)
        expected_dir = tmp_path / "fetch" / url_hash(url)
        assert expected_dir.is_dir()
        assert (expected_dir / "response.html").exists()

    def test_exists_returns_false_then_true(self, tmp_path: Path) -> None:
        url = "https://example.com/new-page"
        assert bronze_exists_by_url("fetch", url, "response", "html", bronze_root=tmp_path) is False
        write_bronze_by_url("fetch", url, "response", "data", "html", bronze_root=tmp_path)
        assert bronze_exists_by_url("fetch", url, "response", "html", bronze_root=tmp_path) is True

    def test_read_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_bronze_by_url("fetch", "https://missing.com", "response", "html", bronze_root=tmp_path)


class TestS3StoreOperations:
    @pytest.fixture()
    def s3_store(self):
        import boto3
        from moto import mock_aws

        with mock_aws():
            client = boto3.client("s3", region_name="us-east-1")
            client.create_bucket(Bucket="test-bronze")

            # Build S3Store without __init__ to avoid creating a client
            # with endpoint_url, which moto cannot intercept.
            store = S3Store.__new__(S3Store)
            store._bucket = "test-bronze"
            store._s3 = client
            yield store

    def test_write_and_read_roundtrip(self, s3_store: S3Store) -> None:
        s3_store.write("hackernews/12345/raw.json", '{"title": "hello"}')
        result = s3_store.read("hackernews/12345/raw.json")
        assert result == '{"title": "hello"}'

    def test_exists_returns_false_when_missing(self, s3_store: S3Store) -> None:
        assert s3_store.exists("hackernews/missing/raw.json") is False

    def test_exists_returns_true_after_write(self, s3_store: S3Store) -> None:
        s3_store.write("hackernews/12345/raw.json", "data")
        assert s3_store.exists("hackernews/12345/raw.json") is True

    def test_read_raises_file_not_found(self, s3_store: S3Store) -> None:
        with pytest.raises(FileNotFoundError):
            s3_store.read("hackernews/nonexistent/raw.json")

    def test_local_path_returns_none(self, s3_store: S3Store) -> None:
        assert s3_store.local_path("hackernews/12345/raw.json") is None

    def test_write_read_unicode(self, s3_store: S3Store) -> None:
        text = "Geist mit Umlauten: aou — emoji"
        s3_store.write("rss/feed1/raw.txt", text)
        assert s3_store.read("rss/feed1/raw.txt") == text

    def test_read_or_none_returns_none_when_missing(self, s3_store: S3Store) -> None:
        assert s3_store.read_or_none("hackernews/missing/raw.json") is None

    def test_read_or_none_returns_text_after_write(self, s3_store: S3Store) -> None:
        s3_store.write("hackernews/12345/raw.json", '{"title": "hello"}')
        result = s3_store.read_or_none("hackernews/12345/raw.json")
        assert result == '{"title": "hello"}'

    def test_write_bytes_and_read_bytes_roundtrip(self, s3_store: S3Store) -> None:
        data = b"\x00\x01\x02\xff audio data"
        s3_store.write_bytes("youtube/vid01/audio.opus", data)
        assert s3_store.read_bytes("youtube/vid01/audio.opus") == data

    def test_read_bytes_raises_file_not_found(self, s3_store: S3Store) -> None:
        with pytest.raises(FileNotFoundError):
            s3_store.read_bytes("youtube/missing/audio.opus")

    def test_list_keys_empty_prefix(self, s3_store: S3Store) -> None:
        assert s3_store.list_keys("hackernews/") == []

    def test_list_keys_returns_matching_keys(self, s3_store: S3Store) -> None:
        s3_store.write("hackernews/111/raw.json", "{}")
        s3_store.write("hackernews/222/raw.json", "{}")
        keys = s3_store.list_keys("hackernews/")
        assert keys == ["hackernews/111/raw.json", "hackernews/222/raw.json"]

    def test_list_keys_filters_by_prefix(self, s3_store: S3Store) -> None:
        s3_store.write("hackernews/111/raw.json", "{}")
        s3_store.write("reddit/222/raw.json", "{}")
        assert s3_store.list_keys("hackernews/") == ["hackernews/111/raw.json"]
        assert s3_store.list_keys("reddit/") == ["reddit/222/raw.json"]


class TestFilesystemStoreOperations:
    @pytest.fixture()
    def fs_store(self, tmp_path: Path) -> FilesystemStore:
        return FilesystemStore(tmp_path)

    def test_write_and_read_roundtrip(self, fs_store: FilesystemStore) -> None:
        fs_store.write("hackernews/12345/raw.json", '{"title": "hello"}')
        result = fs_store.read("hackernews/12345/raw.json")
        assert result == '{"title": "hello"}'

    def test_exists_returns_false_when_missing(self, fs_store: FilesystemStore) -> None:
        assert fs_store.exists("hackernews/missing/raw.json") is False

    def test_exists_returns_true_after_write(self, fs_store: FilesystemStore) -> None:
        fs_store.write("hackernews/12345/raw.json", "data")
        assert fs_store.exists("hackernews/12345/raw.json") is True

    def test_read_raises_file_not_found(self, fs_store: FilesystemStore) -> None:
        with pytest.raises(FileNotFoundError):
            fs_store.read("hackernews/nonexistent/raw.json")

    def test_local_path_returns_actual_path(self, fs_store: FilesystemStore, tmp_path: Path) -> None:
        result = fs_store.local_path("hackernews/12345/raw.json")
        assert result == tmp_path / "hackernews/12345/raw.json"

    def test_write_atomic_no_tmp_lingers(self, fs_store: FilesystemStore, tmp_path: Path) -> None:
        fs_store.write("hackernews/12345/raw.json", "data")
        parent = tmp_path / "hackernews" / "12345"
        tmp_files = list(parent.glob("*.tmp"))
        assert tmp_files == []

    def test_read_or_none_returns_none_when_missing(self, fs_store: FilesystemStore) -> None:
        assert fs_store.read_or_none("hackernews/missing/raw.json") is None

    def test_read_or_none_returns_text_after_write(self, fs_store: FilesystemStore) -> None:
        fs_store.write("hackernews/12345/raw.json", '{"title": "hello"}')
        result = fs_store.read_or_none("hackernews/12345/raw.json")
        assert result == '{"title": "hello"}'

    def test_write_bytes_and_read_bytes_roundtrip(self, fs_store: FilesystemStore) -> None:
        data = b"\x00\x01\x02\xff audio data"
        fs_store.write_bytes("youtube/vid01/audio.opus", data)
        assert fs_store.read_bytes("youtube/vid01/audio.opus") == data

    def test_read_bytes_raises_file_not_found(self, fs_store: FilesystemStore) -> None:
        with pytest.raises(FileNotFoundError):
            fs_store.read_bytes("youtube/missing/audio.opus")

    def test_list_keys_empty_prefix(self, fs_store: FilesystemStore) -> None:
        assert fs_store.list_keys("hackernews/") == []

    def test_list_keys_returns_matching_keys(self, fs_store: FilesystemStore) -> None:
        fs_store.write("hackernews/111/raw.json", "{}")
        fs_store.write("hackernews/222/raw.json", "{}")
        keys = fs_store.list_keys("hackernews/")
        assert keys == ["hackernews/111/raw.json", "hackernews/222/raw.json"]

    def test_list_keys_filters_by_prefix(self, fs_store: FilesystemStore) -> None:
        fs_store.write("hackernews/111/raw.json", "{}")
        fs_store.write("reddit/222/raw.json", "{}")
        assert fs_store.list_keys("hackernews/") == ["hackernews/111/raw.json"]
        assert fs_store.list_keys("reddit/") == ["reddit/222/raw.json"]
