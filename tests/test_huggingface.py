"""Tests for the HuggingFace Papers collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

from aggre.collectors.huggingface import HuggingfaceCollector
from aggre.config import AppConfig, HuggingfaceSource, Settings
from aggre.db import BronzeDiscussion, SilverDiscussion, Source


def _make_config() -> AppConfig:
    return AppConfig(
        huggingface=[HuggingfaceSource(name="HuggingFace Papers")],
        settings=Settings(),
    )


def _make_paper(
    paper_id: str = "2401.12345",
    title: str = "Test Paper",
    summary: str = "A summary of the paper.",
    upvotes: int = 42,
    num_comments: int = 5,
    authors: list[dict] | None = None,  # None = default authors; pass explicit list to override
    github_repo: str | None = "https://github.com/example/repo",
    published_at: str = "2024-01-15T00:00:00.000Z",
):
    return {
        "paper": {
            "id": paper_id,
            "title": title,
            "summary": summary,
            "authors": [{"name": "Alice"}, {"name": "Bob"}] if authors is None else authors,
            "publishedAt": published_at,
            "upvotes": upvotes,
            "numComments": num_comments,
            "githubRepo": github_repo,
        },
        "numComments": num_comments,
    }


def _mock_httpx_client(papers: list[dict]):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = papers
    client.get.return_value = resp
    return client


class TestHuggingfaceCollectorDiscussions:
    def test_stores_papers(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HuggingfaceCollector()

        paper = _make_paper()

        with patch("aggre.collectors.huggingface.httpx.Client") as mock_cls:
            mock_cls.return_value = _mock_httpx_client([paper])
            count = collector.collect(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(BronzeDiscussion)).fetchall()
            assert len(raws) == 1
            assert raws[0].external_id == "2401.12345"
            assert raws[0].source_type == "huggingface"

            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].title == "Test Paper"
            assert items[0].content_text == "A summary of the paper."
            assert items[0].author == "Alice, Bob"
            assert items[0].source_type == "huggingface"
            assert items[0].url == "https://huggingface.co/papers/2401.12345"
            assert items[0].published_at == "2024-01-15T00:00:00.000Z"

            assert items[0].score == 42
            assert items[0].comment_count == 5

            meta = json.loads(items[0].meta)
            assert meta["github_repo"] == "https://github.com/example/repo"

    def test_dedup_across_runs(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HuggingfaceCollector()

        paper = _make_paper()

        with patch("aggre.collectors.huggingface.httpx.Client") as mock_cls:
            mock_cls.return_value = _mock_httpx_client([paper])
            count1 = collector.collect(engine, config, log)
            count2 = collector.collect(engine, config, log)

        assert count1 == 1
        assert count2 == 0

    def test_multiple_papers(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HuggingfaceCollector()

        papers = [
            _make_paper(paper_id="2401.11111", title="First"),
            _make_paper(paper_id="2401.22222", title="Second"),
            _make_paper(paper_id="2401.33333", title="Third"),
        ]

        with patch("aggre.collectors.huggingface.httpx.Client") as mock_cls:
            mock_cls.return_value = _mock_httpx_client(papers)
            count = collector.collect(engine, config, log)

        assert count == 3

    def test_skips_paper_without_id(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HuggingfaceCollector()

        bad_paper = {"paper": {"title": "No ID"}}
        good_paper = _make_paper()

        with patch("aggre.collectors.huggingface.httpx.Client") as mock_cls:
            mock_cls.return_value = _mock_httpx_client([bad_paper, good_paper])
            count = collector.collect(engine, config, log)

        assert count == 1

    def test_no_config_returns_zero(self, engine):
        config = AppConfig(settings=Settings())
        log = MagicMock()
        collector = HuggingfaceCollector()
        assert collector.collect(engine, config, log) == 0

    def test_handles_fetch_error(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HuggingfaceCollector()

        client = MagicMock()
        client.get.side_effect = Exception("network error")

        with patch("aggre.collectors.huggingface.httpx.Client") as mock_cls:
            mock_cls.return_value = client
            count = collector.collect(engine, config, log)

        assert count == 0

    def test_paper_with_no_authors(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HuggingfaceCollector()

        paper = _make_paper(authors=[])

        with patch("aggre.collectors.huggingface.httpx.Client") as mock_cls:
            mock_cls.return_value = _mock_httpx_client([paper])
            count = collector.collect(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert items[0].author is None


class TestHuggingfaceSource:
    def test_creates_source_row(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HuggingfaceCollector()

        with patch("aggre.collectors.huggingface.httpx.Client") as mock_cls:
            mock_cls.return_value = _mock_httpx_client([])
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "huggingface"
            assert rows[0].name == "HuggingFace Papers"

    def test_reuses_existing_source(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = HuggingfaceCollector()

        with patch("aggre.collectors.huggingface.httpx.Client") as mock_cls:
            mock_cls.return_value = _mock_httpx_client([])
            collector.collect(engine, config, log)
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
