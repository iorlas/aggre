"""Tests for SilverContent download and extraction pipeline."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.db import SilverContent
from aggre.tracking.model import StageTracking
from aggre.tracking.ops import upsert_done
from aggre.tracking.status import Stage, StageStatus
from aggre.workflows.webpage import download_content, extract_html_text
from tests.factories import make_config, seed_content
from tests.helpers import assert_tracking

pytestmark = pytest.mark.integration


class TestDownloadContent:
    def test_no_pending_returns_zero(self, engine):
        config = make_config()
        assert download_content(engine, config) == {"downloaded": 0, "cached": 0, "failed": 0, "skipped": 0}

    def test_skips_youtube_urls(self, engine):
        config = make_config()
        seed_content(engine, "https://youtube.com/watch?v=abc", domain="youtube.com")

        stats = download_content(engine, config)
        assert sum(stats.values()) == 0  # YouTube URLs excluded from download query (handled by transcription)

    def test_skips_pdf_urls(self, engine):
        config = make_config()
        seed_content(engine, "https://example.com/paper.pdf", domain="example.com")

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/paper.pdf", Stage.DOWNLOAD, StageStatus.SKIPPED, error_contains="pdf")

    def test_downloads_and_stores_raw_html(self, engine, mock_http):
        config = make_config()
        seed_content(engine, "https://example.com/article", domain="example.com")

        mock_http.get("https://example.com/article").respond(
            text="<html><body><p>Article content here</p></body></html>",
            headers={"content-type": "text/html"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.text is None  # text set by extract phase

        assert_tracking(engine, "webpage", "https://example.com/article", Stage.DOWNLOAD, StageStatus.DONE)

    def test_handles_download_error(self, engine, mock_http):
        config = make_config()
        seed_content(engine, "https://example.com/broken", domain="example.com")

        mock_http.get("https://example.com/broken").mock(side_effect=Exception("Connection refused"))

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(
            engine,
            "webpage",
            "https://example.com/broken",
            Stage.DOWNLOAD,
            StageStatus.FAILED,
            error_contains="Connection refused",
        )

    def test_respects_batch_limit(self, engine):
        config = make_config()

        for i in range(5):
            seed_content(engine, f"https://example.com/paper{i}.pdf", domain="example.com")

        stats = download_content(engine, config, batch_limit=3)
        assert sum(stats.values()) == 3

    def test_skips_already_processed(self, engine):
        config = make_config()
        seed_content(engine, "https://example.com/already-done", text="some text")

        stats = download_content(engine, config)
        assert sum(stats.values()) == 0

    def test_parallel_downloads(self, engine, mock_http):
        config = make_config()

        for i in range(3):
            seed_content(engine, f"https://example.com/article-{i}", domain="example.com")
            mock_http.get(f"https://example.com/article-{i}").respond(
                text="<html><body>content</body></html>",
                headers={"content-type": "text/html"},
            )

        stats = download_content(engine, config, max_workers=3)
        assert sum(stats.values()) == 3

        with engine.connect() as conn:
            tracking_rows = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "webpage",
                    StageTracking.stage == Stage.DOWNLOAD,
                    StageTracking.status == StageStatus.DONE,
                )
            ).fetchall()
            assert len(tracking_rows) == 3

    def test_404_logs_warning_not_exception(self, engine, mock_http, caplog):
        config = make_config()
        seed_content(engine, "https://example.com/gone", domain="example.com")

        mock_http.get("https://example.com/gone").respond(status_code=404)

        with caplog.at_level(logging.WARNING, logger="aggre.workflows.webpage"):
            stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/gone", Stage.DOWNLOAD, StageStatus.FAILED, error_contains="404")

        assert any("webpage_downloader.http_gone" in r.message for r in caplog.records)
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)

    def test_skips_non_text_content_type(self, engine, mock_http):
        config = make_config()
        seed_content(engine, "https://example.com/image.png", domain="example.com")

        mock_http.get("https://example.com/image.png").respond(
            status_code=200,
            headers={"content-type": "image/png"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/image.png", Stage.DOWNLOAD, StageStatus.SKIPPED, error_contains="non_text")

    def test_skips_video_content_type(self, engine, mock_http):
        config = make_config()
        seed_content(engine, "https://example.com/video123", domain="example.com")

        mock_http.get("https://example.com/video123").respond(
            status_code=200,
            headers={"content-type": "video/mp4"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/video123", Stage.DOWNLOAD, StageStatus.SKIPPED, error_contains="non_text")

    def test_fetches_using_original_url(self, engine, mock_http):
        """When original_url is set, HTTP fetch uses it instead of canonical_url."""
        config = make_config()
        seed_content(
            engine,
            "https://example.com/article",
            domain="example.com",
            original_url="https://www.example.com/article",
        )

        # Only mock the original URL — canonical is NOT mocked
        mock_http.get("https://www.example.com/article").respond(
            text="<html><body><p>Content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        # Tracking stored under canonical URL
        assert_tracking(engine, "webpage", "https://example.com/article", Stage.DOWNLOAD, StageStatus.DONE)

    def test_bronze_check_exception_doesnt_crash_batch(self, engine, mock_http):
        """When bronze_exists_by_url raises (e.g., S3 unreachable), the batch
        should continue processing other URLs, not crash the entire run."""
        config = make_config()
        seed_content(engine, "https://example.com/good", domain="example.com")
        seed_content(engine, "https://example.com/s3-broken", domain="example.com")

        mock_http.get("https://example.com/good").respond(
            text="<html><body>ok</body></html>",
            headers={"content-type": "text/html"},
        )
        mock_http.get("https://example.com/s3-broken").respond(
            text="<html><body>ok</body></html>",
            headers={"content-type": "text/html"},
        )

        with patch("aggre.workflows.webpage.bronze_exists_by_url") as mock_bronze:

            def bronze_exists_side_effect(source, url, *args):
                if url == "https://example.com/s3-broken":
                    raise ConnectionError("S3 unreachable")
                return False

            mock_bronze.side_effect = bronze_exists_side_effect

            stats = download_content(engine, config, max_workers=2)

        # Both should be accounted for — batch didn't crash
        assert sum(stats.values()) == 2
        assert_tracking(engine, "webpage", "https://example.com/good", Stage.DOWNLOAD, StageStatus.DONE)
        assert_tracking(engine, "webpage", "https://example.com/s3-broken", Stage.DOWNLOAD, StageStatus.FAILED)

    def test_falls_back_to_canonical_when_no_original_url(self, engine, mock_http):
        """When original_url is NULL, fetches using canonical_url as before."""
        config = make_config()
        seed_content(engine, "https://example.com/article", domain="example.com")

        mock_http.get("https://example.com/article").respond(
            text="<html><body><p>Content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/article", Stage.DOWNLOAD, StageStatus.DONE)


class TestBrowserlessDownload:
    """Tests for the browserless /function endpoint integration."""

    def test_browserless_success_stores_html(self, engine, mock_http):
        config = make_config(browserless_url="http://browserless:3000")
        seed_content(engine, "https://example.com/article", domain="example.com")

        mock_http.post("http://browserless:3000/function").respond(
            json={"data": {"status": 200, "html": "<html><body><p>Real content</p></body></html>"}},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/article", Stage.DOWNLOAD, StageStatus.DONE)

    def test_browserless_403_marks_failed(self, engine, mock_http):
        config = make_config(browserless_url="http://browserless:3000")
        seed_content(engine, "https://example.com/blocked", domain="example.com")

        mock_http.post("http://browserless:3000/function").respond(
            json={"data": {"status": 403, "html": "<html><body>Cloudflare challenge</body></html>"}},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(
            engine,
            "webpage",
            "https://example.com/blocked",
            Stage.DOWNLOAD,
            StageStatus.FAILED,
            error_contains="Cloudflare challenge",
        )

    def test_browserless_429_marks_failed(self, engine, mock_http):
        config = make_config(browserless_url="http://browserless:3000")
        seed_content(engine, "https://example.com/ratelimited", domain="example.com")

        mock_http.post("http://browserless:3000/function").respond(
            json={"data": {"status": 429, "html": "<html><body>Too Many Requests</body></html>"}},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/ratelimited", Stage.DOWNLOAD, StageStatus.FAILED, error_contains="HTTP 429")

    def test_browserless_service_400_stores_response_body(self, engine, mock_http):
        config = make_config(browserless_url="http://browserless:3000")
        seed_content(engine, "https://example.com/ssl-fail", domain="example.com")
        mock_http.post("http://browserless:3000/function").respond(
            status_code=400,
            text="BadRequest: net::ERR_CERT_AUTHORITY_INVALID at https://example.com/ssl-fail",
        )
        download_content(engine, config)
        assert_tracking(
            engine,
            "webpage",
            "https://example.com/ssl-fail",
            Stage.DOWNLOAD,
            StageStatus.FAILED,
            error_contains="ERR_CERT_AUTHORITY_INVALID",
        )

    def test_browserless_service_error(self, engine, mock_http):
        config = make_config(browserless_url="http://browserless:3000")
        seed_content(engine, "https://example.com/service-down", domain="example.com")

        mock_http.post("http://browserless:3000/function").mock(side_effect=Exception("Connection refused"))

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(
            engine, "webpage", "https://example.com/service-down", Stage.DOWNLOAD, StageStatus.FAILED, error_contains="Connection refused"
        )


class TestExtractHtmlText:
    def test_no_downloaded_returns_zero(self, engine):
        config = make_config()
        assert extract_html_text(engine, config) == {"extracted": 0, "failed": 0}

    def test_extracts_text_from_downloaded(self, engine):
        config = make_config()

        html = "<html><body><p>Article content here</p></body></html>"
        seed_content(engine, "https://example.com/article", domain="example.com")
        upsert_done(engine, "webpage", "https://example.com/article", Stage.DOWNLOAD)

        # Write HTML to bronze so extract can read it
        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("webpage", "https://example.com/article", "response", html, "html")

        with (
            patch("aggre.workflows.webpage.trafilatura.extract", return_value="Article content here"),
            patch("aggre.workflows.webpage.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            mock_meta_obj = MagicMock()
            mock_meta_obj.title = "Test Article"
            mock_meta.return_value = mock_meta_obj

            stats = extract_html_text(engine, config)

        assert sum(stats.values()) == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.text == "Article content here"
            assert row.title == "Test Article"

        assert_tracking(engine, "webpage", "https://example.com/article", Stage.EXTRACT, StageStatus.DONE)

    def test_trafilatura_returns_none_marks_failed(self, engine):
        config = make_config()

        html = "<html><body><nav>Menu only</nav></body></html>"
        seed_content(engine, "https://example.com/empty-page", domain="example.com")
        upsert_done(engine, "webpage", "https://example.com/empty-page", Stage.DOWNLOAD)

        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("webpage", "https://example.com/empty-page", "response", html, "html")

        with (
            patch("aggre.workflows.webpage.trafilatura.extract", return_value=None),
            patch("aggre.workflows.webpage.trafilatura.metadata.extract_metadata", return_value=None),
        ):
            stats = extract_html_text(engine, config)

        assert sum(stats.values()) == 1

        assert_tracking(
            engine,
            "webpage",
            "https://example.com/empty-page",
            Stage.EXTRACT,
            StageStatus.FAILED,
            error_contains="no_extractable_content",
        )

        # text must remain NULL
        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.text is None

    def test_handles_extraction_error(self, engine):
        config = make_config()

        seed_content(engine, "https://example.com/bad-html", domain="example.com")
        upsert_done(engine, "webpage", "https://example.com/bad-html", Stage.DOWNLOAD)

        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("webpage", "https://example.com/bad-html", "response", "<html>bad</html>", "html")

        with patch("aggre.workflows.webpage.trafilatura.extract", side_effect=Exception("Parse error")):
            stats = extract_html_text(engine, config)

        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/bad-html", Stage.EXTRACT, StageStatus.FAILED, error_contains="Parse error")

    def test_ignores_undownloaded_content(self, engine):
        config = make_config()

        # No download tracking = not yet downloaded
        seed_content(engine, "https://example.com/still-pending")

        stats = extract_html_text(engine, config)
        assert sum(stats.values()) == 0

    def test_respects_batch_limit(self, engine):
        config = make_config()

        from aggre.utils.bronze import write_bronze_by_url

        for i in range(5):
            url = f"https://example.com/article-{i}"
            seed_content(engine, url, domain="example.com")
            upsert_done(engine, "webpage", url, Stage.DOWNLOAD)
            write_bronze_by_url("webpage", url, "response", f"<html>content {i}</html>", "html")

        with (
            patch("aggre.workflows.webpage.trafilatura.extract", return_value="text"),
            patch("aggre.workflows.webpage.trafilatura.metadata.extract_metadata", return_value=None),
        ):
            stats = extract_html_text(engine, config, batch_limit=3)

        assert sum(stats.values()) == 3

    def test_missing_bronze_file_marks_failed(self, engine):
        config = make_config()

        seed_content(engine, "https://example.com/orphaned", domain="example.com")
        upsert_done(engine, "webpage", "https://example.com/orphaned", Stage.DOWNLOAD)

        # No bronze HTML written — simulates orphaned file after source rename
        stats = extract_html_text(engine, config)

        assert sum(stats.values()) == 1
        assert_tracking(
            engine,
            "webpage",
            "https://example.com/orphaned",
            Stage.EXTRACT,
            StageStatus.FAILED,
            error_contains="Bronze artifact not found",
        )


class TestBrowserlessWaybackFallback:
    """Browserless returns non-404 error → Wayback fallback attempted."""

    def test_browserless_500_triggers_wayback(self, engine, mock_http):
        """Browserless returns 500 → TargetHTTPError → Wayback succeeds → DONE."""
        config = make_config(browserless_url="http://browserless:3000")
        seed_content(engine, "https://example.com/bl-500", domain="example.com")

        mock_http.post("http://browserless:3000/function").respond(
            json={"data": {"status": 500, "html": "<html>Server Error</html>"}},
        )
        mock_http.get("https://archive.org/wayback/available").respond(
            json={
                "archived_snapshots": {
                    "closest": {
                        "available": True,
                        "url": "https://web.archive.org/web/20240101000000/https://example.com/bl-500",
                        "timestamp": "20240101000000",
                        "status": "200",
                    }
                }
            },
        )
        mock_http.get("https://web.archive.org/web/20240101000000/https://example.com/bl-500").respond(
            text="<html><body><p>Archived content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1
        assert_tracking(engine, "webpage", "https://example.com/bl-500", Stage.DOWNLOAD, StageStatus.DONE)


WAYBACK_API_URL = "https://archive.org/wayback/available"
ARCHIVE_URL = "https://web.archive.org/web/20240101000000/https://example.com/article"
WAYBACK_SNAPSHOT = {
    "archived_snapshots": {
        "closest": {
            "available": True,
            "url": ARCHIVE_URL,
            "timestamp": "20240101000000",
            "status": "200",
        }
    }
}
WAYBACK_NO_SNAPSHOT = {"archived_snapshots": {}}


class TestWaybackFallback:
    def test_wayback_on_connection_error(self, engine, mock_http):
        """Direct fetch raises Exception → Wayback returns HTML → status=DONE, bronze written."""
        config = make_config()
        seed_content(engine, "https://example.com/wayback-conn-err", domain="example.com")

        mock_http.get("https://example.com/wayback-conn-err").mock(side_effect=Exception("Connection reset"))
        mock_http.get(WAYBACK_API_URL).respond(json=WAYBACK_SNAPSHOT)
        mock_http.get(ARCHIVE_URL).respond(
            text="<html><body><p>Archived content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/wayback-conn-err", Stage.DOWNLOAD, StageStatus.DONE)

    def test_wayback_on_target_http_error(self, engine, mock_http):
        """Browserless returns 403 → Wayback returns HTML → status=DONE."""
        config = make_config(browserless_url="http://browserless:3000")
        seed_content(engine, "https://example.com/wayback-403", domain="example.com")

        mock_http.post("http://browserless:3000/function").respond(
            json={"data": {"status": 403, "html": "<html>Blocked</html>"}},
        )
        mock_http.get(WAYBACK_API_URL).respond(json=WAYBACK_SNAPSHOT)
        mock_http.get(ARCHIVE_URL).respond(
            text="<html><body><p>Archived content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/wayback-403", Stage.DOWNLOAD, StageStatus.DONE)

    def test_wayback_skipped_for_404(self, engine, mock_http):
        """Browserless returns 404 → no Wayback attempt, status=FAILED."""
        config = make_config(browserless_url="http://browserless:3000")
        seed_content(engine, "https://example.com/gone", domain="example.com")

        mock_http.post("http://browserless:3000/function").respond(
            json={"data": {"status": 404, "html": "<html>Not Found</html>"}},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/gone", Stage.DOWNLOAD, StageStatus.FAILED, error_contains="HTTP 404")

    def test_wayback_unavailable_still_fails(self, engine, mock_http):
        """Direct fetch fails, Wayback API returns no snapshot → status=FAILED."""
        config = make_config()
        seed_content(engine, "https://example.com/no-wayback", domain="example.com")

        mock_http.get("https://example.com/no-wayback").mock(side_effect=Exception("DNS failure"))
        mock_http.get(WAYBACK_API_URL).respond(json=WAYBACK_NO_SNAPSHOT)

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(
            engine, "webpage", "https://example.com/no-wayback", Stage.DOWNLOAD, StageStatus.FAILED, error_contains="DNS failure"
        )

    def test_wayback_on_http_status_error(self, engine, mock_http):
        """Direct fetch returns 500 (httpx.HTTPStatusError) → Wayback succeeds → DONE."""
        config = make_config()
        seed_content(engine, "https://example.com/http-500", domain="example.com")

        mock_http.get("https://example.com/http-500").respond(status_code=500)
        mock_http.get(WAYBACK_API_URL).respond(json=WAYBACK_SNAPSHOT)
        mock_http.get(ARCHIVE_URL).respond(
            text="<html><body><p>Archived content</p></body></html>",
            headers={"content-type": "text/html"},
        )

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(engine, "webpage", "https://example.com/http-500", Stage.DOWNLOAD, StageStatus.DONE)

    def test_wayback_api_error_still_fails(self, engine, mock_http):
        """Direct fetch fails, Wayback API itself errors → status=FAILED."""
        config = make_config()
        seed_content(engine, "https://example.com/wayback-down", domain="example.com")

        mock_http.get("https://example.com/wayback-down").mock(side_effect=Exception("SSL error"))
        mock_http.get(WAYBACK_API_URL).mock(side_effect=Exception("Wayback down"))

        stats = download_content(engine, config)
        assert sum(stats.values()) == 1

        assert_tracking(
            engine, "webpage", "https://example.com/wayback-down", Stage.DOWNLOAD, StageStatus.FAILED, error_contains="SSL error"
        )
