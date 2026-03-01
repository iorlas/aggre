"""Tests for SilverContent download and extraction pipeline."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.dagster_defs.content.job import download_content, extract_html_text
from aggre.db import SilverContent
from aggre.stages.model import StageTracking
from aggre.stages.status import Stage, StageStatus
from aggre.stages.tracking import upsert_done
from tests.factories import make_config, seed_content

pytestmark = pytest.mark.integration


class TestDownloadContent:
    def test_no_pending_returns_zero(self, engine):
        config = make_config()
        assert download_content(engine, config) == 0

    def test_skips_youtube_urls(self, engine):
        config = make_config()
        seed_content(engine, "https://youtube.com/watch?v=abc", domain="youtube.com")

        count = download_content(engine, config)
        assert count == 0  # YouTube URLs excluded from download query (handled by transcription)

    def test_skips_pdf_urls(self, engine):
        config = make_config()
        seed_content(engine, "https://example.com/paper.pdf", domain="example.com")

        count = download_content(engine, config)
        assert count == 1

        with engine.connect() as conn:
            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/paper.pdf",
                    StageTracking.stage == Stage.DOWNLOAD,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.SKIPPED
            assert tracking.error == "skipped:pdf"

    def test_downloads_and_stores_raw_html(self, engine, mock_http):
        config = make_config()
        seed_content(engine, "https://example.com/article", domain="example.com")

        mock_http.get("https://example.com/article").respond(
            text="<html><body><p>Article content here</p></body></html>",
            headers={"content-type": "text/html"},
        )

        count = download_content(engine, config)
        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.text is None  # text set by extract phase

            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/article",
                    StageTracking.stage == Stage.DOWNLOAD,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.DONE

    def test_handles_download_error(self, engine, mock_http):
        config = make_config()
        seed_content(engine, "https://example.com/broken", domain="example.com")

        mock_http.get("https://example.com/broken").mock(side_effect=Exception("Connection refused"))

        count = download_content(engine, config)
        assert count == 1

        with engine.connect() as conn:
            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/broken",
                    StageTracking.stage == Stage.DOWNLOAD,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.FAILED
            assert "Connection refused" in tracking.error

    def test_respects_batch_limit(self, engine):
        config = make_config()

        for i in range(5):
            seed_content(engine, f"https://example.com/paper{i}.pdf", domain="example.com")

        count = download_content(engine, config, batch_limit=3)
        assert count == 3

    def test_skips_already_processed(self, engine):
        config = make_config()
        seed_content(engine, "https://example.com/already-done", text="some text")

        count = download_content(engine, config)
        assert count == 0

    def test_parallel_downloads(self, engine, mock_http):
        config = make_config()

        for i in range(3):
            seed_content(engine, f"https://example.com/article-{i}", domain="example.com")
            mock_http.get(f"https://example.com/article-{i}").respond(
                text="<html><body>content</body></html>",
                headers={"content-type": "text/html"},
            )

        count = download_content(engine, config, max_workers=3)
        assert count == 3

        with engine.connect() as conn:
            tracking_rows = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.stage == Stage.DOWNLOAD,
                    StageTracking.status == StageStatus.DONE,
                )
            ).fetchall()
            assert len(tracking_rows) == 3

    def test_404_logs_warning_not_exception(self, engine, mock_http, caplog):
        config = make_config()
        seed_content(engine, "https://example.com/gone", domain="example.com")

        mock_http.get("https://example.com/gone").respond(status_code=404)

        with caplog.at_level(logging.WARNING, logger="aggre.dagster_defs.content.job"):
            count = download_content(engine, config)
        assert count == 1

        with engine.connect() as conn:
            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/gone",
                    StageTracking.stage == Stage.DOWNLOAD,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.FAILED
            assert "404" in tracking.error

        assert any("content_downloader.http_gone" in r.message for r in caplog.records)
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)

    def test_skips_non_text_content_type(self, engine, mock_http):
        config = make_config()
        seed_content(engine, "https://i.redd.it/image.png", domain="i.redd.it")

        mock_http.get("https://i.redd.it/image.png").respond(
            status_code=200,
            headers={"content-type": "image/png"},
        )

        count = download_content(engine, config)
        assert count == 1

        with engine.connect() as conn:
            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://i.redd.it/image.png",
                    StageTracking.stage == Stage.DOWNLOAD,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.SKIPPED
            assert tracking.error == "skipped:non_text"

    def test_skips_video_content_type(self, engine, mock_http):
        config = make_config()
        seed_content(engine, "https://v.redd.it/video123", domain="v.redd.it")

        mock_http.get("https://v.redd.it/video123").respond(
            status_code=200,
            headers={"content-type": "video/mp4"},
        )

        count = download_content(engine, config)
        assert count == 1

        with engine.connect() as conn:
            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://v.redd.it/video123",
                    StageTracking.stage == Stage.DOWNLOAD,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.SKIPPED
            assert tracking.error == "skipped:non_text"


class TestExtractHtmlText:
    def test_no_downloaded_returns_zero(self, engine):
        config = make_config()
        assert extract_html_text(engine, config) == 0

    def test_extracts_text_from_downloaded(self, engine):
        config = make_config()

        html = "<html><body><p>Article content here</p></body></html>"
        seed_content(engine, "https://example.com/article", domain="example.com")
        upsert_done(engine, "content", "https://example.com/article", Stage.DOWNLOAD)

        # Write HTML to bronze so extract can read it
        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("content", "https://example.com/article", "response", html, "html")

        with (
            patch("aggre.dagster_defs.content.job.trafilatura.extract", return_value="Article content here"),
            patch("aggre.dagster_defs.content.job.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            mock_meta_obj = MagicMock()
            mock_meta_obj.title = "Test Article"
            mock_meta.return_value = mock_meta_obj

            count = extract_html_text(engine, config)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.text == "Article content here"
            assert row.title == "Test Article"

            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/article",
                    StageTracking.stage == Stage.EXTRACT,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.DONE

    def test_handles_extraction_error(self, engine):
        config = make_config()

        seed_content(engine, "https://example.com/bad-html", domain="example.com")
        upsert_done(engine, "content", "https://example.com/bad-html", Stage.DOWNLOAD)

        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("content", "https://example.com/bad-html", "response", "<html>bad</html>", "html")

        with patch("aggre.dagster_defs.content.job.trafilatura.extract", side_effect=Exception("Parse error")):
            count = extract_html_text(engine, config)

        assert count == 1

        with engine.connect() as conn:
            tracking = conn.execute(
                sa.select(StageTracking).where(
                    StageTracking.source == "content",
                    StageTracking.external_id == "https://example.com/bad-html",
                    StageTracking.stage == Stage.EXTRACT,
                )
            ).fetchone()
            assert tracking is not None
            assert tracking.status == StageStatus.FAILED
            assert "Parse error" in tracking.error

    def test_ignores_undownloaded_content(self, engine):
        config = make_config()

        # No download tracking = not yet downloaded
        seed_content(engine, "https://example.com/still-pending")

        count = extract_html_text(engine, config)
        assert count == 0

    def test_respects_batch_limit(self, engine):
        config = make_config()

        from aggre.utils.bronze import write_bronze_by_url

        for i in range(5):
            url = f"https://example.com/article-{i}"
            seed_content(engine, url, domain="example.com")
            upsert_done(engine, "content", url, Stage.DOWNLOAD)
            write_bronze_by_url("content", url, "response", f"<html>content {i}</html>", "html")

        with (
            patch("aggre.dagster_defs.content.job.trafilatura.extract", return_value="text"),
            patch("aggre.dagster_defs.content.job.trafilatura.metadata.extract_metadata", return_value=None),
        ):
            count = extract_html_text(engine, config, batch_limit=3)

        assert count == 3
