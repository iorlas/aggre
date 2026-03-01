"""Tests for the HuggingFace Papers collector."""

from __future__ import annotations

import json

import pytest

from aggre.collectors.huggingface.collector import HuggingfaceCollector
from aggre.collectors.huggingface.config import HuggingfaceConfig, HuggingfaceSource
from tests.factories import hf_paper, make_config
from tests.helpers import collect, get_observations, get_sources

pytestmark = pytest.mark.integration

HF_API = "https://huggingface.co/api/daily_papers"


class TestHuggingfaceCollectorDiscussions:
    def test_stores_papers(self, engine, mock_http, log):
        mock_http.get(HF_API).respond(json=[hf_paper()])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        assert count == 1

        items = get_observations(engine)
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

    def test_dedup_across_runs(self, engine, mock_http, log):
        mock_http.get(HF_API).respond(json=[hf_paper()])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count1 = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)
        count2 = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        assert count1 == 1
        assert count2 == 1  # collect_references returns all API items; dedup is in upsert

    def test_multiple_papers(self, engine, mock_http, log):
        papers = [
            hf_paper(paper_id="2401.11111", title="First"),
            hf_paper(paper_id="2401.22222", title="Second"),
            hf_paper(paper_id="2401.33333", title="Third"),
        ]
        mock_http.get(HF_API).respond(json=papers)

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        assert count == 3

    def test_skips_paper_without_id(self, engine, mock_http, log):
        bad_paper = {"paper": {"title": "No ID"}}
        mock_http.get(HF_API).respond(json=[bad_paper, hf_paper()])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        assert count == 1

    def test_no_config_returns_zero(self, engine, mock_http, log):
        config = make_config(huggingface=HuggingfaceConfig(sources=[]))
        assert collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log) == 0

    def test_handles_fetch_error(self, engine, mock_http, log):
        mock_http.get(HF_API).mock(side_effect=Exception("network error"))

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        assert count == 0

    def test_paper_with_no_authors(self, engine, mock_http, log):
        mock_http.get(HF_API).respond(json=[hf_paper(authors=[])])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        count = collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        assert count == 1

        items = get_observations(engine)
        assert items[0].author is None


class TestHuggingfaceSource:
    def test_creates_source_row(self, engine, mock_http, log):
        mock_http.get(HF_API).respond(json=[])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "huggingface"
        assert rows[0].name == "HuggingFace Papers"

    def test_reuses_existing_source(self, engine, mock_http, log):
        mock_http.get(HF_API).respond(json=[])

        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HuggingFace Papers")]))
        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)
        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings, log)

        assert len(get_sources(engine)) == 1
