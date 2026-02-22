"""Tests for bronze-aware HTTP wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from aggre.utils.bronze import url_hash, write_bronze, write_bronze_json
from aggre.utils.bronze_http import fetch_item_json, fetch_url_text


def _mock_json_response(data: object, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response returning JSON data."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    resp.raise_for_status.return_value = None
    return resp


def _mock_text_response(text: str, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response returning text."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


def _make_client(response: MagicMock) -> MagicMock:
    """Create a mock httpx.Client that returns the given response on get()."""
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = response
    return client


class TestFetchItemJson:
    def test_cache_miss(self, tmp_path: Path) -> None:
        """No cache — fetches from HTTP, writes to bronze, returns data."""
        data = {"title": "Test Story", "points": 42}
        client = _make_client(_mock_json_response(data))
        log = MagicMock()

        result = fetch_item_json(
            "hackernews",
            "12345",
            "https://hn.algolia.com/api/v1/items/12345",
            client,
            log,
            bronze_root=tmp_path,
        )

        assert result == data
        client.get.assert_called_once_with("https://hn.algolia.com/api/v1/items/12345")
        log.info.assert_called_once()

    def test_cache_hit(self, tmp_path: Path) -> None:
        """Pre-populated bronze — HTTP not called, returns cached data."""
        data = {"title": "Cached Story", "points": 99}
        write_bronze_json("hackernews", "12345", data, bronze_root=tmp_path)

        client = _make_client(_mock_json_response({"should": "not be returned"}))
        log = MagicMock()

        result = fetch_item_json(
            "hackernews",
            "12345",
            "https://hn.algolia.com/api/v1/items/12345",
            client,
            log,
            bronze_root=tmp_path,
        )

        assert result == data
        client.get.assert_not_called()

    def test_writes_to_correct_path(self, tmp_path: Path) -> None:
        """Verify file written at {source_type}/{external_id}/raw.json."""
        data = {"id": "abc", "value": 1}
        client = _make_client(_mock_json_response(data))
        log = MagicMock()

        fetch_item_json("reddit", "abc", "https://reddit.com/api/abc", client, log, bronze_root=tmp_path)

        path = tmp_path / "reddit" / "abc" / "raw.json"
        assert path.exists()
        parsed = json.loads(path.read_text())
        assert parsed == data

    def test_raises_on_http_error(self, tmp_path: Path) -> None:
        """HTTP error propagates to caller."""
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=resp,
        )
        client = _make_client(resp)
        log = MagicMock()

        with pytest.raises(httpx.HTTPStatusError):
            fetch_item_json(
                "hackernews",
                "99999",
                "https://hn.algolia.com/api/v1/items/99999",
                client,
                log,
                bronze_root=tmp_path,
            )


class TestFetchUrlText:
    def test_cache_miss(self, tmp_path: Path) -> None:
        """Fetches HTML, writes to bronze, returns text."""
        html = "<html><body>Hello World</body></html>"
        client = _make_client(_mock_text_response(html))
        log = MagicMock()

        result = fetch_url_text(
            "fetch",
            "https://example.com/article",
            client,
            log,
            bronze_root=tmp_path,
        )

        assert result == html
        client.get.assert_called_once_with("https://example.com/article")
        log.info.assert_called_once()

    def test_cache_hit(self, tmp_path: Path) -> None:
        """Pre-populated bronze — no HTTP call, returns cached text."""
        html = "<html><body>Cached</body></html>"
        url = "https://example.com/cached-page"
        write_bronze(
            "fetch",
            url_hash(url),
            "response",
            html,
            "html",
            bronze_root=tmp_path,
        )

        client = _make_client(_mock_text_response("<html>wrong</html>"))
        log = MagicMock()

        result = fetch_url_text("fetch", url, client, log, bronze_root=tmp_path)

        assert result == html
        client.get.assert_not_called()

    def test_uses_url_hash(self, tmp_path: Path) -> None:
        """Verify directory name is the URL hash."""
        html = "<html>content</html>"
        url = "https://example.com/some/long/path?query=1"
        client = _make_client(_mock_text_response(html))
        log = MagicMock()

        fetch_url_text("fetch", url, client, log, bronze_root=tmp_path)

        expected_dir = tmp_path / "fetch" / url_hash(url)
        assert expected_dir.is_dir()
        assert (expected_dir / "response.html").exists()
