"""Tests for URL normalization and SilverContent management."""

from __future__ import annotations

import sqlalchemy as sa

from aggre.db import SilverContent
from aggre.urls import ensure_content, normalize_url
from aggre.utils.urls import extract_domain


class TestNormalizeUrl:
    def test_basic_normalization(self):
        assert normalize_url("  HTTP://WWW.Example.COM/page/  ") == "https://example.com/page"

    def test_strips_tracking_params(self):
        result = normalize_url("https://example.com/page?utm_source=twitter&utm_medium=social&real=1")
        assert result == "https://example.com/page?real=1"

    def test_strips_fbclid(self):
        result = normalize_url("https://example.com/page?fbclid=abc123")
        assert result == "https://example.com/page"

    def test_sorts_params(self):
        result = normalize_url("https://example.com/page?z=1&a=2")
        assert result == "https://example.com/page?a=2&z=1"

    def test_removes_fragment(self):
        result = normalize_url("https://example.com/page#section")
        assert result == "https://example.com/page"

    def test_forces_https(self):
        result = normalize_url("http://example.com/page")
        assert result == "https://example.com/page"

    def test_removes_www(self):
        result = normalize_url("https://www.example.com/page")
        assert result == "https://example.com/page"

    def test_removes_trailing_slash(self):
        result = normalize_url("https://example.com/page/")
        assert result == "https://example.com/page"

    def test_empty_url(self):
        assert normalize_url("") is None
        assert normalize_url(None) is None

    def test_non_http_scheme(self):
        assert normalize_url("ftp://example.com") is None
        assert normalize_url("mailto:user@example.com") is None

    # Domain-specific tests
    def test_arxiv_strips_version(self):
        result = normalize_url("https://arxiv.org/abs/2301.12345v2")
        assert result == "https://arxiv.org/abs/2301.12345"

    def test_arxiv_strips_query(self):
        result = normalize_url("https://arxiv.org/abs/2301.12345?context=cs")
        assert result == "https://arxiv.org/abs/2301.12345"

    def test_youtube_keeps_v_param(self):
        result = normalize_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")
        assert result == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_youtu_be_short_url(self):
        result = normalize_url("https://youtu.be/dQw4w9WgXcQ")
        assert result == "https://youtube.com/watch?v=dQw4w9WgXcQ"

    def test_github_removes_git_suffix(self):
        result = normalize_url("https://github.com/user/repo.git")
        assert result == "https://github.com/user/repo"

    def test_github_removes_tree_branch(self):
        result = normalize_url("https://github.com/user/repo/tree/main/")
        assert result == "https://github.com/user/repo"

    def test_reddit_normalizes_to_comments(self):
        result = normalize_url("https://www.reddit.com/r/python/comments/abc123/some_title/?sort=top")
        assert result == "https://reddit.com/r/python/comments/abc123"

    def test_hn_keeps_id_param(self):
        result = normalize_url("https://news.ycombinator.com/item?id=12345&goto=something")
        assert result == "https://news.ycombinator.com/item?id=12345"

    def test_medium_removes_source(self):
        result = normalize_url("https://medium.com/article?source=twitter&other=1")
        assert result == "https://medium.com/article?other=1"


class TestExtractDomain:
    def test_basic(self):
        assert extract_domain("https://example.com/page") == "example.com"

    def test_strips_www(self):
        assert extract_domain("https://www.example.com/page") == "example.com"

    def test_empty(self):
        assert extract_domain("") is None
        assert extract_domain(None) is None

    def test_subdomain(self):
        assert extract_domain("https://blog.example.com/post") == "blog.example.com"


class TestEnsureContent:
    def test_creates_new_content(self, engine):
        with engine.begin() as conn:
            cid = ensure_content(conn, "https://example.com/article")

        assert cid is not None

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverContent)).fetchone()
            assert row.canonical_url == "https://example.com/article"
            assert row.domain == "example.com"
            assert row.fetch_status == "pending"

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
