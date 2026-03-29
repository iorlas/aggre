"""Tests for the extract_one step of the webpage workflow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.db import SilverContent
from aggre.utils.bronze import write_bronze_by_url
from aggre.workflows.webpage import extract_one
from tests.factories import seed_content

pytestmark = pytest.mark.integration


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

        write_bronze_by_url("webpage", "https://example.com/bad-html", "response", "<html>bad</html>", "html")

        with patch("aggre.workflows.webpage.trafilatura.extract", side_effect=Exception("Parse error")):
            with pytest.raises(Exception, match="Parse error"):
                extract_one(engine, content_id)
