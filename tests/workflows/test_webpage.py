"""Tests for per-item webpage download and extraction."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.db import SilverContent
from aggre.workflows.webpage import download_one, extract_one
from tests.factories import make_config, seed_content

pytestmark = pytest.mark.integration


class TestDownloadOne:
    def test_skips_when_text_already_set(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/done", text="already processed")

        assert download_one(engine, config, content_id) == "skipped"

    def test_skips_nonexistent_content(self, engine):
        config = make_config()
        assert download_one(engine, config, 99999) == "skipped"

    def test_skips_pdf_urls(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/paper.pdf", domain="example.com")

        assert download_one(engine, config, content_id) == "skipped"

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_downloads_and_stores_html(self, _mock_bronze, engine, mock_http):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/download-test-1", domain="example.com")

        mock_http.get("https://example.com/download-test-1").respond(
            text="<html><body><p>Article content here</p></body></html>",
            headers={"content-type": "text/html"},
        )

        assert download_one(engine, config, content_id) == "downloaded"

        # text should still be NULL (extraction is separate)
        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent).where(SilverContent.id == content_id)).fetchone()
            assert row.text is None

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_handles_download_error(self, _mock_bronze, engine, mock_http):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/broken", domain="example.com")

        mock_http.get("https://example.com/broken").mock(side_effect=Exception("Connection refused"))

        with pytest.raises(Exception, match="Connection refused"):
            download_one(engine, config, content_id)

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_404_returns_skipped(self, _mock_bronze, engine, mock_http, caplog):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/gone", domain="example.com")

        mock_http.get("https://example.com/gone").respond(status_code=404)

        with caplog.at_level(logging.WARNING, logger="aggre.workflows.webpage"):
            result = download_one(engine, config, content_id)

        assert result == "skipped"
        assert any("webpage_downloader.http_gone" in r.message for r in caplog.records)

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_skips_non_text_content_type(self, _mock_bronze, engine, mock_http):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/image.png", domain="example.com")

        mock_http.get("https://example.com/image.png").respond(
            status_code=200,
            headers={"content-type": "image/png"},
        )

        assert download_one(engine, config, content_id) == "skipped"

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_fetches_using_original_url(self, _mock_bronze, engine, mock_http):
        """When original_url is set, HTTP fetch uses it instead of canonical_url."""
        config = make_config()
        content_id = seed_content(
            engine,
            "https://example.com/original-url-test",
            domain="example.com",
            original_url="https://www.example.com/original-url-test",
        )

        mock_http.get("https://www.example.com/original-url-test").respond(
            text="<html><body><p>Content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        assert download_one(engine, config, content_id) == "downloaded"

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_falls_back_to_canonical_when_no_original_url(self, _mock_bronze, engine, mock_http):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/canonical-fallback-test", domain="example.com")

        mock_http.get("https://example.com/canonical-fallback-test").respond(
            text="<html><body><p>Content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        assert download_one(engine, config, content_id) == "downloaded"

    def test_bronze_cache_hit(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/cached", domain="example.com")

        with patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=True):
            assert download_one(engine, config, content_id) == "cached"

    def test_bronze_check_exception_propagates(self, engine):
        """When bronze_exists_by_url raises, the error propagates (Hatchet retries)."""
        config = make_config()
        content_id = seed_content(engine, "https://example.com/s3-broken", domain="example.com")

        with patch("aggre.workflows.webpage.bronze_exists_by_url", side_effect=ConnectionError("S3 unreachable")):
            with pytest.raises(ConnectionError, match="S3 unreachable"):
                download_one(engine, config, content_id)


class TestBrowserlessDownload:
    """Tests for the Browserless /chromium/function endpoint integration."""

    def _fn_response(self, status: int, html: str) -> dict:
        return {"data": {"status": status, "html": html}}

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_browserless_success_stores_html(self, _mock_bronze, engine, mock_http):
        config = make_config(browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/browserless-test", domain="example.com")

        mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(200, "<html><body><p>Real content</p></body></html>"),
        )

        assert download_one(engine, config, content_id) == "downloaded"

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_browserless_target_403_raises(self, _mock_bronze, engine, mock_http):
        """Function returns target HTTP 403 — raises for Hatchet retry."""
        config = make_config(browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/blocked", domain="example.com")

        mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(403, "<html>Forbidden</html>"),
        )

        with pytest.raises(Exception):
            download_one(engine, config, content_id)

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_browserless_navigation_error_raises(self, _mock_bronze, engine, mock_http):
        """Navigation failure (timeout, DNS) returns structured error — raises for Wayback fallback."""
        config = make_config(browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/nav-error", domain="example.com")

        mock_http.post("http://browserless:3000/chromium/function").respond(
            json={"data": {"status": 0, "html": "", "error": "net::ERR_CONNECTION_REFUSED"}},
        )

        with pytest.raises(Exception, match="Navigation failed"):
            download_one(engine, config, content_id)

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_browserless_service_error_raises(self, _mock_bronze, engine, mock_http):
        config = make_config(browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/service-down", domain="example.com")

        mock_http.post("http://browserless:3000/chromium/function").mock(side_effect=Exception("Connection refused"))

        with pytest.raises(Exception, match="Connection refused"):
            download_one(engine, config, content_id)

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_browserless_sends_proxy_launch_arg(self, _mock_bronze, engine, mock_http):
        """When proxy_url is set, launch.args includes --proxy-server for chromium."""
        config = make_config(browserless_url="http://browserless:3000", proxy_url="socks5://proxy:1080")
        content_id = seed_content(engine, "https://example.com/proxy-test", domain="example.com")

        route = mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(200, "<html><body>ok</body></html>"),
        )

        assert download_one(engine, config, content_id) == "downloaded"

        import json as _json

        body = _json.loads(route.calls[0].request.content)
        assert body.get("launch", {}).get("args") == ["--proxy-server=socks5://proxy:1080"]

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_browserless_no_launch_arg_without_proxy(self, _mock_bronze, engine, mock_http):
        """When proxy_url is empty, no launch key is sent to browserless."""
        config = make_config(browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/no-proxy-test", domain="example.com")

        route = mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(200, "<html><body>ok</body></html>"),
        )

        assert download_one(engine, config, content_id) == "downloaded"

        import json as _json

        body = _json.loads(route.calls[0].request.content)
        assert "launch" not in body


class TestExtractOne:
    def test_returns_not_found_for_nonexistent_content(self, engine):
        assert extract_one(engine, 99999) == "not_found"

    def test_skips_already_processed(self, engine):
        content_id = seed_content(engine, "https://example.com/done", text="existing text")
        assert extract_one(engine, content_id) == "already_done"

    def test_skips_when_bronze_missing(self, engine):
        content_id = seed_content(engine, "https://example.com/no-bronze", domain="example.com")
        assert extract_one(engine, content_id) == "skipped"

    def test_extracts_text_from_downloaded(self, engine):
        content_id = seed_content(engine, "https://example.com/article", domain="example.com")

        html = "<html><body><p>Article content here</p></body></html>"
        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("webpage", "https://example.com/article", "response", html, "html")

        with (
            patch("aggre.workflows.webpage.trafilatura.extract", return_value="Article content here"),
            patch("aggre.workflows.webpage.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            mock_meta_obj = MagicMock()
            mock_meta_obj.title = "Test Article"
            mock_meta.return_value = mock_meta_obj

            result = extract_one(engine, content_id)

        assert result == "extracted"

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent).where(SilverContent.id == content_id)).fetchone()
            assert row.text == "Article content here"
            assert row.title == "Test Article"

    def test_trafilatura_returns_none(self, engine):
        content_id = seed_content(engine, "https://example.com/empty-page", domain="example.com")

        html = "<html><body><nav>Menu only</nav></body></html>"
        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("webpage", "https://example.com/empty-page", "response", html, "html")

        with (
            patch("aggre.workflows.webpage.trafilatura.extract", return_value=None),
            patch("aggre.workflows.webpage.trafilatura.metadata.extract_metadata", return_value=None),
        ):
            result = extract_one(engine, content_id)

        assert result == "no_content"

        # text must remain NULL
        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent).where(SilverContent.id == content_id)).fetchone()
            assert row.text is None

    def test_handles_extraction_error(self, engine):
        content_id = seed_content(engine, "https://example.com/bad-html", domain="example.com")

        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("webpage", "https://example.com/bad-html", "response", "<html>bad</html>", "html")

        with patch("aggre.workflows.webpage.trafilatura.extract", side_effect=Exception("Parse error")):
            with pytest.raises(Exception, match="Parse error"):
                extract_one(engine, content_id)
