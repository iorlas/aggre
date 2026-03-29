"""Tests for the HuggingFace Papers collector."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from aggre.collectors.huggingface.collector import HuggingfaceCollector
from aggre.collectors.huggingface.config import HuggingfaceConfig, HuggingfaceSource
from tests.factories import hf_paper, make_config
from tests.helpers import collect, get_discussions, get_sources

pytestmark = pytest.mark.integration

HF_API = "https://huggingface.co/api/daily_papers"


class TestHuggingfaceCollectorDiscussions:
    def test_stores_papers(self, engine, mock_http):
        mock_http.get(HF_API).respond(json=[hf_paper()])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        assert count == 1

        items = get_discussions(engine)
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

    def test_dedup_across_runs(self, engine, mock_http):
        mock_http.get(HF_API).respond(json=[hf_paper()])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count1 = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)
        count2 = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        assert count1 == 1
        assert count2 == 1  # collect_discussions returns all API items; dedup is in upsert

    def test_multiple_papers(self, engine, mock_http):
        papers = [
            hf_paper(paper_id="2401.11111", title="First"),
            hf_paper(paper_id="2401.22222", title="Second"),
            hf_paper(paper_id="2401.33333", title="Third"),
        ]
        mock_http.get(HF_API).respond(json=papers)

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        assert count == 3

    def test_skips_paper_without_id(self, engine, mock_http):
        bad_paper = {"paper": {"title": "No ID"}}
        mock_http.get(HF_API).respond(json=[bad_paper, hf_paper()])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        assert count == 1

    def test_no_config_returns_zero(self, engine, mock_http):
        config = make_config(huggingface=HuggingfaceConfig(sources=[]))
        assert collect(HuggingfaceCollector(), engine, config.huggingface, config.settings) == 0

    def test_handles_fetch_error(self, engine, mock_http):
        mock_http.get(HF_API).mock(side_effect=Exception("network error"))

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        assert count == 0

    def test_paper_with_no_authors(self, engine, mock_http):
        mock_http.get(HF_API).respond(json=[hf_paper(authors=[])])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        assert count == 1

        items = get_discussions(engine)
        assert items[0].author is None


class TestHuggingfaceSource:
    def test_creates_source_row(self, engine, mock_http):
        mock_http.get(HF_API).respond(json=[])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "huggingface"
        assert rows[0].name == "HuggingFace Papers"

    def test_reuses_existing_source(self, engine, mock_http):
        mock_http.get(HF_API).respond(json=[])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)
        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        assert len(get_sources(engine)) == 1


class TestHuggingfaceCollectorProxy:
    def test_collect_calls_get_proxy_once(self, engine, mock_http):
        """collect_discussions() should call get_proxy() once (per-run)."""
        mock_http.get(HF_API).respond(json=[hf_paper()])

        with patch(
            "aggre.collectors.huggingface.collector.get_proxy", return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"}
        ) as mock_gp:
            config = make_config(
                huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]),
                proxy_api_url="http://proxy-hub:8000",
            )
            collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        mock_gp.assert_called_once_with("http://proxy-hub:8000", protocol="socks5")

    def test_collect_no_proxy_when_api_url_empty(self, engine, mock_http):
        """collect_discussions() should not call get_proxy() when proxy_api_url is empty."""
        mock_http.get(HF_API).respond(json=[])

        with patch("aggre.collectors.huggingface.collector.get_proxy") as mock_gp:
            config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
            collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        mock_gp.assert_not_called()

    def test_collect_proceeds_when_get_proxy_returns_none(self, engine, mock_http):
        """collect_discussions() should proceed without proxy when get_proxy() returns None."""
        mock_http.get(HF_API).respond(json=[hf_paper()])

        with patch("aggre.collectors.huggingface.collector.get_proxy", return_value=None):
            config = make_config(
                huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]),
                proxy_api_url="http://proxy-hub:8000",
            )
            count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        assert count == 1
