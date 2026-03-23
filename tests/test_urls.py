"""Tests for URL normalization and SilverContent management."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from aggre.db import SilverContent
from aggre.urls import ensure_content, normalize_url
from aggre.utils.urls import extract_domain

pytestmark = pytest.mark.unit


class TestNormalizeUrl:
    def test_basic_normalization(self):
        assert normalize_url("  HTTP://WWW.Example.COM/page/  ") == "https://example.com/page"

    @pytest.mark.parametrize(
        ("input_url", "expected"),
        [
            ("https://example.com/page?utm_source=twitter&utm_medium=social&real=1", "https://example.com/page?real=1"),
            ("https://example.com/page?fbclid=abc123", "https://example.com/page"),
            ("https://example.com/page?z=1&a=2", "https://example.com/page?a=2&z=1"),
            ("https://example.com/page#section", "https://example.com/page"),
            ("http://example.com/page", "https://example.com/page"),
            ("https://www.example.com/page", "https://example.com/page"),
            ("https://example.com/page/", "https://example.com/page"),
        ],
        ids=[
            "strips_tracking_params",
            "strips_fbclid",
            "sorts_params",
            "removes_fragment",
            "forces_https",
            "removes_www",
            "removes_trailing_slash",
        ],
    )
    def test_normalization_rules(self, input_url, expected):
        assert normalize_url(input_url) == expected

    def test_empty_url(self):
        assert normalize_url("") is None
        assert normalize_url(None) is None

    def test_non_http_scheme(self):
        assert normalize_url("ftp://example.com") is None
        assert normalize_url("mailto:user@example.com") is None

    @pytest.mark.parametrize(
        ("input_url", "expected"),
        [
            ("https://arxiv.org/abs/2301.12345v2", "https://arxiv.org/abs/2301.12345"),
            ("https://arxiv.org/abs/2301.12345?context=cs", "https://arxiv.org/abs/2301.12345"),
            (
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
                "https://youtube.com/watch?v=dQw4w9WgXcQ",
            ),
            ("https://youtu.be/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
            ("https://github.com/user/repo.git", "https://github.com/user/repo"),
            ("https://github.com/user/repo/tree/main/", "https://github.com/user/repo"),
            ("https://www.reddit.com/r/python/comments/abc123/some_title/?sort=top", "https://reddit.com/r/python/comments/abc123"),
            ("https://news.ycombinator.com/item?id=12345&goto=something", "https://news.ycombinator.com/item?id=12345"),
            ("https://medium.com/article?source=twitter&other=1", "https://medium.com/article?other=1"),
        ],
        ids=[
            "arxiv_strips_version",
            "arxiv_strips_query",
            "youtube_keeps_v_param",
            "youtu_be_short_url",
            "github_removes_git_suffix",
            "github_removes_tree_branch",
            "reddit_normalizes_to_comments",
            "hn_keeps_id_param",
            "medium_removes_source",
        ],
    )
    def test_domain_specific_normalization(self, input_url, expected):
        assert normalize_url(input_url) == expected

    def test_youtube_url_without_v_param(self):
        """YouTube URL without v param (channel URL) → query cleared."""
        result = normalize_url("https://www.youtube.com/channel/UCxxx123")
        assert result == "https://youtube.com/channel/UCxxx123"

    def test_reddit_url_not_matching_comments_pattern(self):
        """Reddit URL not matching /r/*/comments/*/ → cleaned but path preserved."""
        result = normalize_url("https://www.reddit.com/user/testuser")
        assert result == "https://reddit.com/user/testuser"


class TestExtractDomain:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://example.com/page", "example.com"),
            ("https://www.example.com/page", "example.com"),
            ("https://blog.example.com/post", "blog.example.com"),
        ],
        ids=["basic", "strips_www", "subdomain"],
    )
    def test_extracts_domain(self, url, expected):
        assert extract_domain(url) == expected

    def test_empty(self):
        assert extract_domain("") is None
        assert extract_domain(None) is None


class TestEnsureContent:
    def test_creates_new_content(self, engine):
        with engine.begin() as conn:
            cid = ensure_content(conn, "https://example.com/article")

        assert cid is not None

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.canonical_url == "https://example.com/article"
            assert row.domain == "example.com"
            assert row.text is None  # needs processing

    def test_returns_existing(self, engine):
        with engine.begin() as conn:
            cid1 = ensure_content(conn, "https://example.com/article")
            cid2 = ensure_content(conn, "https://example.com/article")

        assert cid1 == cid2

    def test_normalizes_before_lookup(self, engine):
        with engine.begin() as conn:
            cid1 = ensure_content(conn, "https://www.example.com/article/")
            cid2 = ensure_content(conn, "https://example.com/article")

        assert cid1 == cid2

    def test_returns_none_for_invalid(self, engine):
        with engine.begin() as conn:
            cid = ensure_content(conn, "not-a-url")

        assert cid is None

    def test_returns_none_for_empty(self, engine):
        with engine.begin() as conn:
            cid = ensure_content(conn, "")

        assert cid is None
