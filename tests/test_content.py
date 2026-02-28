"""Tests for SilverContent download and extraction pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aggre.config import AppConfig
from aggre.dagster_defs.content.job import download_content, extract_html_text
from aggre.db import SilverContent
from aggre.settings import Settings


def _seed_content(
    engine,
    url: str,
    domain: str | None = None,
    fetched_at: str | None = None,
    text: str | None = None,
    error: str | None = None,
):
    with engine.begin() as conn:
        stmt = pg_insert(SilverContent).values(
            canonical_url=url,
            domain=domain,
            fetched_at=fetched_at,
            text=text,
            error=error,
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
            assert row.error == "skipped:youtube"

    def test_skips_pdf_urls(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/paper.pdf", domain="example.com")

        count = download_content(engine, config, log)
        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.error == "skipped:pdf"

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

        with patch("aggre.dagster_defs.content.job.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.fetched_at is not None
            assert row.error is None
            assert row.text is None  # text set by extract phase

    def test_handles_download_error(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/broken", domain="example.com")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("Connection refused")

        with patch("aggre.dagster_defs.content.job.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.error is not None
            assert "Connection refused" in row.error

    def test_respects_batch_limit(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        for i in range(5):
            _seed_content(engine, f"https://youtube.com/watch?v=vid{i}", domain="youtube.com")

        count = download_content(engine, config, log, batch_limit=3)
        assert count == 3

    def test_skips_already_processed(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/already-done", text="some text")

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

        with patch("aggre.dagster_defs.content.job.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log, max_workers=3)

        assert count == 3

        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(SilverContent).where(SilverContent.fetched_at.isnot(None), SilverContent.error.is_(None))
            ).fetchall()
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

        with patch("aggre.dagster_defs.content.job.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.error is not None
            assert "404" in row.error

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

        with patch("aggre.dagster_defs.content.job.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.error == "skipped:non_text"

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

        with patch("aggre.dagster_defs.content.job.httpx.Client", return_value=mock_client):
            count = download_content(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.error == "skipped:non_text"


class TestExtractHtmlText:
    def test_no_downloaded_returns_zero(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()
        assert extract_html_text(engine, config, log) == 0

    def test_extracts_text_from_downloaded(self, engine, tmp_path):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        html = "<html><body><p>Article content here</p></body></html>"
        _seed_content(engine, "https://example.com/article", domain="example.com", fetched_at="2024-01-01T00:00:00Z")

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

            count = extract_html_text(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.text == "Article content here"
            assert row.title == "Test Article"
            assert row.fetched_at is not None
            assert row.error is None

    def test_handles_extraction_error(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        _seed_content(engine, "https://example.com/bad-html", domain="example.com", fetched_at="2024-01-01T00:00:00Z")

        from aggre.utils.bronze import write_bronze_by_url

        write_bronze_by_url("content", "https://example.com/bad-html", "response", "<html>bad</html>", "html")

        with patch("aggre.dagster_defs.content.job.trafilatura.extract", side_effect=Exception("Parse error")):
            count = extract_html_text(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.error is not None
            assert "Parse error" in row.error

    def test_ignores_undownloaded_content(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()

        # No fetched_at = not yet downloaded
        _seed_content(engine, "https://example.com/still-pending")

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
                fetched_at="2024-01-01T00:00:00Z",
            )
            write_bronze_by_url("content", url, "response", f"<html>content {i}</html>", "html")

        with (
            patch("aggre.dagster_defs.content.job.trafilatura.extract", return_value="text"),
            patch("aggre.dagster_defs.content.job.trafilatura.metadata.extract_metadata", return_value=None),
        ):
            count = extract_html_text(engine, config, log, batch_limit=3)

        assert count == 3
