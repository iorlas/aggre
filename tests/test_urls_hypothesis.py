"""Property-based tests for URL normalization using Hypothesis."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aggre.urls import normalize_url


@pytest.mark.unit
class TestNormalizeUrlProperties:
    @given(st.from_regex(r"https?://[a-z]+\.[a-z]{2,4}/[a-z0-9/]*", fullmatch=True))
    @settings(max_examples=200)
    def test_idempotent(self, url):
        """normalize_url(normalize_url(x)) == normalize_url(x)."""
        result = normalize_url(url)
        if result is not None:
            assert normalize_url(result) == result

    @given(st.from_regex(r"https?://[a-z]+\.[a-z]{2,4}/[a-z0-9/]*", fullmatch=True))
    @settings(max_examples=200)
    def test_always_https_or_none(self, url):
        """Result is always https:// or None."""
        result = normalize_url(url)
        assert result is None or result.startswith("https://")

    @given(st.from_regex(r"https?://[a-z]+\.[a-z]{2,4}/\?utm_source=abc&real=1", fullmatch=True))
    @settings(max_examples=200)
    def test_no_tracking_params(self, url):
        """Result never contains utm_ parameters."""
        result = normalize_url(url)
        if result:
            assert "utm_" not in result

    @given(st.from_regex(r"https?://www\.[a-z]+\.[a-z]{2,4}/[a-z0-9/]*", fullmatch=True))
    @settings(max_examples=200)
    def test_removes_www(self, url):
        """Result never starts with www."""
        result = normalize_url(url)
        if result:
            assert "://www." not in result

    @given(st.from_regex(r"https?://[a-z]+\.[a-z]{2,4}/[a-z0-9/]*#[a-z]+", fullmatch=True))
    @settings(max_examples=100)
    def test_no_fragment(self, url):
        """Result never contains a fragment (#)."""
        result = normalize_url(url)
        if result:
            assert "#" not in result


@pytest.mark.unit
class TestNormalizeUrlDomainSpecific:
    @given(st.from_regex(r"https?://arxiv\.org/abs/\d{4}\.\d{5}(v\d)?", fullmatch=True))
    @settings(max_examples=100)
    def test_arxiv_strips_version(self, url):
        """arxiv URLs should strip version suffix."""
        result = normalize_url(url)
        if result:
            assert not result[-2:].startswith("v") or not result[-1].isdigit()

    @given(
        st.sampled_from(
            [
                "https://youtube.com/watch?v=abc123",
                "https://youtu.be/abc123",
                "https://m.youtube.com/watch?v=abc123",
                "https://www.youtube.com/watch?v=abc123",
            ]
        )
    )
    def test_youtube_normalizes_to_canonical(self, url):
        """All YouTube URL variants normalize to youtube.com/watch?v=ID."""
        result = normalize_url(url)
        assert result is not None
        assert result.startswith("https://youtube.com/watch?v=")

    @given(
        st.one_of(
            st.from_regex(r"https?://github\.com/[a-z]+/[a-z]+\.git", fullmatch=True),
            st.from_regex(r"https?://github\.com/[a-z]+/[a-z]+/tree/[a-z]+/?", fullmatch=True),
            st.from_regex(r"https?://github\.com/[a-z]+/[a-z]+", fullmatch=True),
        )
    )
    @settings(max_examples=100)
    def test_github_removes_git_and_tree(self, url):
        """GitHub URLs should strip .git suffix and /tree/branch."""
        result = normalize_url(url)
        if result:
            assert not result.endswith(".git")
            assert "/tree/" not in result
