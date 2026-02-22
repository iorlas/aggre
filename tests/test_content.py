"""Tests for SilverContent download and extraction pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.config import AppConfig
from aggre.db import SilverContent
from aggre.pipeline.content_downloader import download_content
from aggre.pipeline.content_extractor import extract_html_text
from aggre.settings import Settings


def _seed_content(engine, url: str, domain: str | None = None, fetch_status: str = "pending"):
    with engine.begin() as conn:
        stmt = pg_insert(SilverContent).values(
            canonical_url=url,
            domain=domain,
            fetch_status=fetch_status,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["canonical_url"])
        result = conn.execute(stmt)
        return result.inserted_primary_key[0]


class TestDownloadContent:
    def test_no_pending_returns_zero(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()
        assert download_content(engine, config, log) == 0

    def test_skips_youtube_urls(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://youtube.com/watch?v=abc", domain="youtube.com")

        count = download_content(engine, config, log)
        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "skipped"

    def test_skips_pdf_urls(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/paper.pdf", domain="example.com")

        count = download_content(engine, config, log)
        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "skipped"

    def test_downloads_and_stores_raw_html(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/article", domain="example.com")

        mock_resp = MagicMock()
        mock_resp.text = "<html><body><p>Article content here</p></body></html>"
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("aggre.pipeline.content_downloader.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "downloaded"
            assert row.fetched_at is not None

    def test_handles_download_error(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/broken", domain="example.com")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("Connection refused")

        with patch("aggre.pipeline.content_downloader.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "failed"
            assert "Connection refused" in row.fetch_error

    def test_respects_batch_limit(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        for i in range(5):
            _seed_content(engine, f"https://youtube.com/watch?v=vid{i}", domain="youtube.com")

        count = download_content(engine, config, log, batch_limit=3)
        assert count == 3

    def test_skips_already_fetched(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/already-done", fetch_status="fetched")

        count = download_content(engine, config, log)
        assert count == 0

    def test_parallel_downloads(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        for i in range(3):
            _seed_content(engine, f"https://example.com/article-{i}", domain="example.com")

        mock_resp = MagicMock()
        mock_resp.text = "<html><body>content</body></html>"
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("aggre.pipeline.content_downloader.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log, max_workers=3)

        assert count == 3

        with engine.connect() as conn:
            rows = conn.execute(sa.select(SilverContent).where(SilverContent.fetch_status == "downloaded")).fetchall()
            assert len(rows) == 3

    def test_404_logs_warning_not_exception(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/gone", domain="example.com")

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("aggre.pipeline.content_downloader.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "failed"
            assert "404" in row.fetch_error

        log.warning.assert_called()
        log.exception.assert_not_called()

    def test_skips_non_text_content_type(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://i.redd.it/image.png", domain="i.redd.it")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "image/png"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("aggre.pipeline.content_downloader.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "skipped"

    def test_skips_video_content_type(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://v.redd.it/video123", domain="v.redd.it")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "video/mp4"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("aggre.pipeline.content_downloader.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "skipped"


class TestExtractHtmlText:
    def test_no_downloaded_returns_zero(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()
        assert extract_html_text(engine, config, log) == 0

    def test_extracts_text_from_downloaded(self, engine, tmp_path):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        html = "<html><body><p>Article content here</p></body></html>"
        _seed_content(engine, "https://example.com/article", domain="example.com", fetch_status="downloaded")

        # Write HTML to bronze so extract can read it
        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("content", "https://example.com/article", "response", html, "html")

        with (
            patch("aggre.pipeline.content_extractor.trafilatura.extract", return_value="Article content here"),
            patch("aggre.pipeline.content_extractor.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            mock_meta_obj = MagicMock()
            mock_meta_obj.title = "Test Article"
            mock_meta.return_value = mock_meta_obj

            count = extract_html_text(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "fetched"
            assert row.body_text == "Article content here"
            assert row.title == "Test Article"
            assert row.fetched_at is not None

    def test_handles_extraction_error(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/bad-html", domain="example.com", fetch_status="downloaded")

        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("content", "https://example.com/bad-html", "response", "<html>bad</html>", "html")

        with patch("aggre.pipeline.content_extractor.trafilatura.extract", side_effect=Exception("Parse error")):
            count = extract_html_text(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetch_status == "failed"
            assert "Parse error" in row.fetch_error

    def test_ignores_pending_content(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/still-pending", fetch_status="pending")

        count = extract_html_text(engine, config, log)
        assert count == 0

    def test_respects_batch_limit(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        from aggre.utils.bronze import write_bronze_by_url

        for i in range(5):
            url = f"https://example.com/article-{i}"
            _seed_content(
                engine,
                url,
                domain="example.com",
                fetch_status="downloaded",
            )
            write_bronze_by_url("content", url, "response", f"<html>content {i}</html>", "html")

        with (
            patch("aggre.pipeline.content_extractor.trafilatura.extract", return_value="text"),
            patch("aggre.pipeline.content_extractor.trafilatura.metadata.extract_metadata", return_value=None),
        ):
            count = extract_html_text(engine, config, log, batch_limit=3)

        assert count == 3
