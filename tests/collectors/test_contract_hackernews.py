"""Contract tests for the Hacker News Algolia API.

Verify that real API responses can be parsed by our collector.
Cassettes recorded with ``pytest --record-mode=once`` and replayed in CI.
"""

from __future__ import annotations

import pytest

from aggre.utils.http import create_http_client

pytestmark = pytest.mark.contract

HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


class TestHackernewsSearchContract:
    """Verify the search_by_date endpoint returns fields our collector depends on."""

    @pytest.mark.vcr()
    def test_search_response_structure(self) -> None:
        """Response contains ``hits`` array with required story fields."""
        with create_http_client() as client:
            resp = client.get(f"{HN_ALGOLIA_BASE}/search_by_date?tags=story&hitsPerPage=1")
            resp.raise_for_status()

        data = resp.json()
        assert "hits" in data
        assert len(data["hits"]) >= 1

        hit = data["hits"][0]
        assert "objectID" in hit
        assert "title" in hit
        assert "author" in hit
        assert "points" in hit
        assert "num_comments" in hit
        assert "created_at" in hit
        # url may be None for self-posts — key must exist
        assert "url" in hit

    @pytest.mark.vcr()
    def test_search_objectid_is_string(self) -> None:
        """objectID is a string (our collector calls ``str(hit.get('objectID')))``)."""
        with create_http_client() as client:
            resp = client.get(f"{HN_ALGOLIA_BASE}/search_by_date?tags=story&hitsPerPage=1")
            resp.raise_for_status()

        hit = resp.json()["hits"][0]
        assert isinstance(hit["objectID"], str)


class TestHackernewsItemContract:
    """Verify the items endpoint returns fields our comment fetcher depends on."""

    @pytest.mark.vcr()
    def test_item_response_structure(self) -> None:
        """Response contains ``id`` and ``children`` array."""
        # Use a well-known HN item (the original HN announcement)
        with create_http_client() as client:
            resp = client.get(f"{HN_ALGOLIA_BASE}/items/1")
            resp.raise_for_status()

        data = resp.json()
        assert "id" in data
        assert "children" in data
        assert isinstance(data["children"], list)

    @pytest.mark.vcr()
    def test_item_child_structure(self) -> None:
        """Children contain fields our collector extracts for comment storage."""
        with create_http_client() as client:
            resp = client.get(f"{HN_ALGOLIA_BASE}/items/1")
            resp.raise_for_status()

        data = resp.json()
        children = data["children"]
        if len(children) == 0:
            pytest.skip("Item has no children to verify")

        child = children[0]
        assert "id" in child
        assert "author" in child
        assert "text" in child
        assert "created_at" in child
