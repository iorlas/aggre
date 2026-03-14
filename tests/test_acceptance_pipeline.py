"""Acceptance tests: full pipeline flow and content fetcher integration.

These tests exercise multi-component flows that cross workflow boundaries.
Single-component tests belong in tests/collectors/ or tests/workflows/.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.collectors.rss.collector import RssCollector
from aggre.collectors.rss.config import RssConfig, RssSource
from aggre.db import SilverContent, SilverDiscussion
from aggre.workflows.webpage import download_one, extract_one
from tests.conftest import dummy_http_client
from tests.factories import (
    make_config,
    rss_entry,
    rss_feed,
    seed_content,
)
from tests.helpers import collect, get_contents

pytestmark = pytest.mark.acceptance


class TestSchemaConstraints:
    """Verify that legacy comment tables do not exist."""

    def test_no_bronze_or_silver_comments_tables(self, engine):
        inspector = sa.inspect(engine)
        tables = inspector.get_table_names()
        assert "bronze_comments" not in tables
        assert "silver_comments" not in tables


class TestFullPipelineFlow:
    """Simulate fetch pipeline: collect -> fetch_content across workflow boundaries."""

    def test_rss_pipeline_creates_full_chain(self, engine, mock_http):
        config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://blog.example.com/feed.xml")]))

        # Step 1: Collect RSS posts
        entry = rss_entry(
            id="rss-1",
            title="Great Article",
            link="https://blog.example.com/great-article",
            summary="A teaser summary",
        )
        feed = rss_feed([entry])

        with (
            patch("aggre.collectors.rss.collector.create_http_client", dummy_http_client),
            patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed),
        ):
            rss = RssCollector()
            count = collect(rss, engine, config.rss, config.settings)

        assert count == 1

        # Verify SilverDiscussion exists with content_id
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc is not None
            assert disc.title == "Great Article"
            assert disc.content_id is not None

            # Verify SilverContent exists in unprocessed state
            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == disc.content_id)).fetchone()
            assert content is not None
            assert content.text is None
            assert "blog.example.com" in content.canonical_url

        content_id = disc.content_id

        # Step 2: Download content for the collected item
        mock_http.get("https://blog.example.com/great-article").respond(
            text="<html><body><p>Full article body here</p></body></html>",
            headers={"content-type": "text/html"},
        )

        with patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False):
            result = download_one(engine, config, content_id)
        assert result.status == "downloaded"

        # Verify intermediate state: downloaded but not yet extracted
        content = get_contents(engine)[0]
        assert content.text is None

        # Step 3: Extract text from downloaded HTML
        with (
            patch("aggre.workflows.webpage.trafilatura.extract", return_value="Full article body here"),
            patch("aggre.workflows.webpage.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            mock_meta_obj = MagicMock()
            mock_meta_obj.title = "Great Article - Full"
            mock_meta.return_value = mock_meta_obj

            result = extract_one(engine, content_id)

        assert result.status == "extracted"

        # Verify full chain: SilverDiscussion -> SilverContent
        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.content_id is not None

            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == disc.content_id)).fetchone()
            assert content.text == "Full article body here"
            assert content.title == "Great Article - Full"


class TestContentFetcherIntegration:
    """Content fetcher: per-item processing with different content states."""

    def test_mixed_statuses(self, engine, mock_http):
        """One normal, one YouTube (skipped by transcription), one failing."""
        config = make_config()

        good_id = seed_content(engine, "https://example.com/good", domain="example.com")
        seed_content(engine, "https://youtube.com/watch?v=vid1", domain="youtube.com")
        bad_id = seed_content(engine, "https://bad.example.com/broken", domain="bad.example.com")

        mock_http.get("https://example.com/good").respond(
            text="<html><body>Good content</body></html>",
            headers={"content-type": "text/html"},
        )
        mock_http.get("https://bad.example.com/broken").mock(side_effect=Exception("DNS failure"))

        # Download each item individually (as Hatchet would)
        with patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False):
            assert download_one(engine, config, good_id).status == "downloaded"

        # YouTube is skipped at domain level by the workflow (download_one still processes it)
        # In real usage, the event self-filter in the Hatchet task would skip this
        # Here we just verify the function works

        with patch("aggre.workflows.webpage.bronze_exists_by_url", return_value=False):
            with pytest.raises(Exception, match="DNS failure"):
                download_one(engine, config, bad_id)

        with engine.connect() as conn:
            good = conn.execute(sa.select(SilverContent).where(SilverContent.id == good_id)).fetchone()
            assert good.text is None  # downloaded but not yet extracted

        # Now extract the downloaded one
        with (
            patch("aggre.workflows.webpage.trafilatura.extract", return_value="Good body"),
            patch("aggre.workflows.webpage.trafilatura.metadata.extract_metadata") as mock_meta,
        ):
            meta_obj = MagicMock()
            meta_obj.title = "Good Title"
            mock_meta.return_value = meta_obj

            result = extract_one(engine, good_id)

        assert result.status == "extracted"

        with engine.connect() as conn:
            good = conn.execute(sa.select(SilverContent).where(SilverContent.id == good_id)).fetchone()
            assert good.text == "Good body"
            assert good.title == "Good Title"
