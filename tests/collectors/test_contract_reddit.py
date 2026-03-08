"""Contract tests for the Reddit JSON API.

Verify that real API responses can be parsed by our collector.
Cassettes recorded with ``pytest --record-mode=once`` and replayed in CI.
"""

from __future__ import annotations

import pytest

from aggre.utils.http import create_http_client

pytestmark = pytest.mark.contract


class TestRedditListingContract:
    """Verify the subreddit listing endpoint returns fields our collector depends on."""

    @pytest.mark.vcr()
    def test_listing_response_structure(self) -> None:
        """Response contains ``data.children`` array with post data."""
        with create_http_client() as client:
            resp = client.get("https://www.reddit.com/r/python/hot.json?limit=1")
            resp.raise_for_status()

        data = resp.json()
        assert "data" in data
        assert "children" in data["data"]
        assert len(data["data"]["children"]) >= 1

    @pytest.mark.vcr()
    def test_post_fields(self) -> None:
        """Each child has the fields our collector extracts."""
        with create_http_client() as client:
            resp = client.get("https://www.reddit.com/r/python/hot.json?limit=1")
            resp.raise_for_status()

        child = resp.json()["data"]["children"][0]
        assert child["kind"] == "t3"

        post = child["data"]
        assert "name" in post
        assert "title" in post
        assert "author" in post
        assert "selftext" in post
        assert "permalink" in post
        assert "created_utc" in post
        assert "score" in post
        assert "num_comments" in post
        assert "subreddit" in post
        assert "url" in post
        assert "is_self" in post

    @pytest.mark.vcr()
    def test_name_is_prefixed(self) -> None:
        """Post ``name`` has the ``t3_`` prefix our collector uses."""
        with create_http_client() as client:
            resp = client.get("https://www.reddit.com/r/python/hot.json?limit=1")
            resp.raise_for_status()

        post = resp.json()["data"]["children"][0]["data"]
        assert post["name"].startswith("t3_")
