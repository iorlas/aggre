"""Tests for the ArXiv collector."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from aggre.collectors.arxiv.collector import ArxivCollector
from aggre.collectors.arxiv.config import ArxivConfig, ArxivSource
from aggre.db import SilverContent, SilverDiscussion, Source
from aggre.settings import Settings
from tests.conftest import dummy_http_client as _dummy_http_client
from tests.factories import arxiv_entry, rss_feed
from tests.helpers import collect, get_discussions, get_sources

pytestmark = pytest.mark.integration


class TestArxivCollector:
    def test_stores_paper(self, engine):
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        entry = arxiv_entry()
        feed = rss_feed([entry])

        with (
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            count = collect(collector, engine, config, settings)

        assert count == 1

        rows = get_discussions(engine)
        assert len(rows) == 1
        assert rows[0].source_type == "arxiv"
        assert rows[0].external_id == "2602.23360"
        assert rows[0].title == "Test Paper: A Novel Approach"
        assert rows[0].author == "Alice Researcher"
        assert rows[0].content_text == "We present a novel approach to testing."

        meta = json.loads(rows[0].meta)
        assert "cs.AI" in meta["categories"]
        assert meta["arxiv_url"] == "https://arxiv.org/abs/2602.23360v1"

    def test_bozo_feed_continues(self, engine, caplog):
        """Bozo feed with entries → warning logged, entries still processed."""
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        entry = arxiv_entry()
        feed = rss_feed([entry])
        feed.bozo = True
        feed.bozo_exception = Exception("malformed XML")

        with (
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            count = collect(collector, engine, config, settings)

        assert count == 1
        assert len(get_discussions(engine)) == 1

    def test_empty_feed_updates_last_fetched(self, engine):
        """No entries → updates last_fetched_at, continues."""
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        feed = rss_feed([])

        with (
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            collect(collector, engine, config, settings)

        with engine.connect() as conn:
            row = conn.execute(sa.select(Source.last_fetched_at)).fetchone()
            assert row[0] is not None

    def test_missing_paper_id_skips_entry(self, engine):
        """Entry with link that doesn't match paper ID regex → skipped."""
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        entry = arxiv_entry(link="https://arxiv.org/list/cs.AI/recent")
        feed = rss_feed([entry])

        with (
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            count = collect(collector, engine, config, settings)

        assert count == 0
        assert len(get_discussions(engine)) == 0

    def test_category_dedup_in_meta(self, engine):
        """Feed category already in entry tags → not duplicated."""
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        entry = arxiv_entry(tags=[{"term": "cs.AI"}, {"term": "cs.LG"}])
        feed = rss_feed([entry])

        with (
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            collect(collector, engine, config, settings)

        rows = get_discussions(engine)
        meta = json.loads(rows[0].meta)
        # cs.AI should appear only once despite being both feed category and entry tag
        assert meta["categories"].count("cs.AI") == 1
        assert "cs.LG" in meta["categories"]

    def test_content_created_for_paper_url(self, engine):
        """SilverContent row created for the paper page URL."""
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        entry = arxiv_entry()
        feed = rss_feed([entry])

        with (
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            collect(collector, engine, config, settings)

        with engine.connect() as conn:
            disc = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert disc.content_id is not None

            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == disc.content_id)).fetchone()
            assert content is not None
            assert "arxiv.org" in content.canonical_url

    def test_source_row_created(self, engine):
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        feed = rss_feed([arxiv_entry()])

        with (
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            collect(collector, engine, config, settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "arxiv"
        assert rows[0].name == "ArXiv CS.AI"

    def test_multiple_entries(self, engine):
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        entries = [
            arxiv_entry(link="https://arxiv.org/abs/2602.11111v1", title="Paper A"),
            arxiv_entry(link="https://arxiv.org/abs/2602.22222v1", title="Paper B"),
        ]
        feed = rss_feed(entries)

        with (
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            count = collect(collector, engine, config, settings)

        assert count == 2
        assert len(get_discussions(engine)) == 2


class TestArxivCollectorProxy:
    def test_collect_calls_get_proxy_once(self, engine):
        """collect_discussions() should call get_proxy() once (per-run)."""
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings(proxy_api_url="http://proxy-hub:8000")

        feed = rss_feed([arxiv_entry()])

        with (
            patch("aggre.collectors.arxiv.collector.get_proxy", return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"}) as mock_gp,
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            collect(collector, engine, config, settings)

        mock_gp.assert_called_once_with("http://proxy-hub:8000", protocol="socks5")

    def test_collect_no_proxy_when_api_url_empty(self, engine):
        """collect_discussions() should not call get_proxy() when proxy_api_url is empty."""
        config = ArxivConfig(sources=[ArxivSource(name="ArXiv CS.AI", category="cs.AI")])
        settings = Settings()

        feed = rss_feed([])

        with (
            patch("aggre.collectors.arxiv.collector.get_proxy") as mock_gp,
            patch("aggre.collectors.arxiv.collector.create_http_client", _dummy_http_client),
            patch("aggre.collectors.arxiv.collector.feedparser.parse", return_value=feed),
        ):
            collector = ArxivCollector()
            collect(collector, engine, config, settings)

        mock_gp.assert_not_called()
