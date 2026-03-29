"""Tests for per-item webpage download and extraction."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.db import SilverContent
from aggre.utils.http import create_http_client
from aggre.workflows.webpage import JINA_SKIP_DOMAINS, _fetch_via_jina, download_one, extract_one
from tests.factories import make_config, seed_content

pytestmark = pytest.mark.integration


class TestDownloadOne:
    def test_skips_when_text_already_set(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/done", text="already processed")

        result = download_one(engine, config, content_id)
        assert result.status == "skipped"
        assert result.reason == "already_done"

    def test_skips_nonexistent_content(self, engine):
        config = make_config()
        result = download_one(engine, config, 99999)
        assert result.status == "skipped"
        assert result.reason == "not_found"

    def test_skips_pdf_urls(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/paper.pdf", domain="example.com")

        assert download_one(engine, config, content_id).status == "skipped"

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_downloads_and_stores_html(self, _mock_bronze, engine, mock_http):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/download-test-1", domain="example.com")

        mock_http.get("https://example.com/download-test-1").respond(
            text="<html><body><p>Article content here</p></body></html>",
            headers={"content-type": "text/html"},
        )

        assert download_one(engine, config, content_id).status == "downloaded"

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

        assert result.status == "skipped"
        assert any("webpage_downloader.http_gone" in r.message for r in caplog.records)

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_skips_non_text_content_type(self, _mock_bronze, engine, mock_http):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/image.png", domain="example.com")

        mock_http.get("https://example.com/image.png").respond(
            status_code=200,
            headers={"content-type": "image/png"},
        )

        assert download_one(engine, config, content_id).status == "skipped"

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

        assert download_one(engine, config, content_id).status == "downloaded"

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_falls_back_to_canonical_when_no_original_url(self, _mock_bronze, engine, mock_http):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/canonical-fallback-test", domain="example.com")

        mock_http.get("https://example.com/canonical-fallback-test").respond(
            text="<html><body><p>Content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        assert download_one(engine, config, content_id).status == "downloaded"

    def test_bronze_cache_hit(self, engine):
        config = make_config()
        content_id = seed_content(engine, "https://example.com/cached", domain="example.com")

        with patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=True):
            assert download_one(engine, config, content_id).status == "cached"

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

        assert download_one(engine, config, content_id).status == "downloaded"

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_browserless_target_403_raises(self, _mock_bronze, engine, mock_http):
        """Function returns target HTTP 403 — raises for Hatchet retry."""
        config = make_config(browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/blocked", domain="example.com")

        mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(403, "<html>Forbidden</html>"),
        )

        with pytest.raises(Exception, match=r"Forbidden|403"):
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
    @patch(
        "aggre.workflows.webpage.get_proxy",
        return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"},
    )
    def test_browserless_sends_proxy_launch_arg(self, _mock_get, _mock_bronze, engine, mock_http):
        """When proxy API returns a proxy, launch args are passed as query parameter."""
        config = make_config(browserless_url="http://browserless:3000", proxy_api_url="http://proxy-api:8080")
        content_id = seed_content(engine, "https://example.com/proxy-test", domain="example.com")

        route = mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(200, "<html><body>ok</body></html>"),
        )

        assert download_one(engine, config, content_id).status == "downloaded"

        import json as _json

        url = route.calls[0].request.url
        launch_param = _json.loads(str(url.params.get("launch")))
        assert launch_param["args"] == ["--proxy-server=socks5://1.2.3.4:1080"]

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    def test_browserless_no_launch_arg_without_proxy(self, _mock_bronze, engine, mock_http):
        """When no proxy_api_url is set, no launch query param is sent to browserless."""
        config = make_config(browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/no-proxy-test", domain="example.com")

        route = mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(200, "<html><body>ok</body></html>"),
        )

        assert download_one(engine, config, content_id).status == "downloaded"

        url = route.calls[0].request.url
        assert "launch" not in str(url)


class TestProxyAPIIntegration:
    """Tests for Proxy API integration in download_one."""

    def _fn_response(self, status: int, html: str) -> dict:
        return {"data": {"status": status, "html": html}}

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch(
        "aggre.workflows.webpage.get_proxy",
        return_value={"addr": "1.2.3.4:8080", "protocol": "socks5"},
    )
    def test_uses_proxy_api_when_configured(self, mock_get, _mock_bronze, engine, mock_http):
        """When proxy_api_url is set, get_proxy is called and result used."""
        config = make_config(proxy_api_url="http://proxy-api:8080", browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/proxy-api-test", domain="example.com")

        route = mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(200, "<html><body>ok</body></html>"),
        )

        result = download_one(engine, config, content_id)
        assert result.status == "downloaded"
        mock_get.assert_called_once_with("http://proxy-api:8080", protocol="socks5")

        import json as _json

        url = route.calls[0].request.url
        launch_param = _json.loads(str(url.params.get("launch")))
        assert launch_param["args"] == ["--proxy-server=socks5://1.2.3.4:8080"]

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch("aggre.workflows.webpage.report_failure")
    @patch(
        "aggre.workflows.webpage.get_proxy",
        return_value={"addr": "1.2.3.4:8080", "protocol": "socks5"},
    )
    def test_reports_failure_on_download_error(self, _mock_get, mock_report, _mock_bronze, engine, mock_http):
        """On download failure, report_failure is called before re-raising."""
        config = make_config(proxy_api_url="http://proxy-api:8080", browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/proxy-fail-test", domain="example.com")

        mock_http.post("http://browserless:3000/chromium/function").mock(
            side_effect=Exception("Connection refused"),
        )

        with pytest.raises(Exception, match="Connection refused"):
            download_one(engine, config, content_id)

        mock_report.assert_called_once_with("http://proxy-api:8080", "1.2.3.4:8080")

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch("aggre.workflows.webpage.get_proxy", return_value=None)
    def test_proceeds_without_proxy_when_api_returns_none(self, _mock_get, _mock_bronze, engine, mock_http):
        """When Proxy API returns None, proceeds without proxy."""
        config = make_config(proxy_api_url="http://proxy-api:8080", browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/no-proxy-avail-test", domain="example.com")

        route = mock_http.post("http://browserless:3000/chromium/function").respond(
            json=self._fn_response(200, "<html><body>ok</body></html>"),
        )

        result = download_one(engine, config, content_id)
        assert result.status == "downloaded"

        url = route.calls[0].request.url
        assert "launch" not in str(url)

    @patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False)
    @patch("aggre.workflows.webpage.report_failure")
    @patch("aggre.workflows.webpage.get_proxy", return_value=None)
    def test_no_failure_report_when_no_proxy_addr(self, _mock_get, mock_report, _mock_bronze, engine, mock_http):
        """When proxy API returned None, failure report is not sent."""
        config = make_config(proxy_api_url="http://proxy-api:8080", browserless_url="http://browserless:3000")
        content_id = seed_content(engine, "https://example.com/no-addr-fail-test", domain="example.com")

        mock_http.post("http://browserless:3000/chromium/function").mock(
            side_effect=Exception("Connection refused"),
        )

        with pytest.raises(Exception, match="Connection refused"):
            download_one(engine, config, content_id)

        mock_report.assert_not_called()


@pytest.mark.unit
class TestFetchViaJina:
    """Tests for the Jina Reader fallback function."""

    def test_returns_markdown_on_success(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/article").respond(
            text="# Article Title\n\nSome article content that is long enough to pass the length check easily.",
            headers={"content-type": "text/plain"},
        )

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/article", "https://r.jina.ai")

        assert result is not None
        assert "Article Title" in result

    def test_returns_none_on_http_error(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/broken").respond(status_code=500)

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/broken", "https://r.jina.ai")

        assert result is None

    def test_returns_none_on_empty_response(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/empty").respond(text="   ")

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/empty", "https://r.jina.ai")

        assert result is None

    def test_returns_none_on_short_response(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/short").respond(text="Blocked")

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/short", "https://r.jina.ai")

        assert result is None

    def test_returns_none_on_connection_error(self, mock_http):
        mock_http.get("https://r.jina.ai/https://example.com/down").mock(
            side_effect=Exception("Connection refused"),
        )

        with create_http_client() as client:
            result = _fetch_via_jina(client, "https://example.com/down", "https://r.jina.ai")

        assert result is None

    def test_skip_domains_includes_reddit_hn_lobsters(self):
        assert "reddit.com" in JINA_SKIP_DOMAINS
        assert "www.reddit.com" in JINA_SKIP_DOMAINS
        assert "old.reddit.com" in JINA_SKIP_DOMAINS
        assert "news.ycombinator.com" in JINA_SKIP_DOMAINS
        assert "lobste.rs" in JINA_SKIP_DOMAINS


class TestExtractOne:
    def test_returns_not_found_for_nonexistent_content(self, engine):
        result = extract_one(engine, 99999)
        assert result.status == "skipped"
        assert result.reason == "not_found"

    def test_skips_already_processed(self, engine):
        content_id = seed_content(engine, "https://example.com/done", text="existing text")
        result = extract_one(engine, content_id)
        assert result.status == "skipped"
        assert result.reason == "already_done"

    def test_skips_when_bronze_missing(self, engine):
        content_id = seed_content(engine, "https://example.com/no-bronze", domain="example.com")
        result = extract_one(engine, content_id)
        assert result.status == "skipped"
        assert result.reason == "no_bronze"

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

        assert result.status == "extracted"

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

        assert result.status == "no_content"

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
