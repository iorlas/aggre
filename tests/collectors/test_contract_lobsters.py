"""Contract tests for the Lobsters JSON API.

Verify that real API responses can be parsed by our collector.
Cassettes recorded with ``pytest --record-mode=once`` and replayed in CI.
"""

from __future__ import annotations

import pytest

from aggre.utils.http import create_http_client

pytestmark = pytest.mark.contract


class TestLobstersHottestContract:
    """Verify the hottest endpoint returns fields our collector depends on."""

    @pytest.mark.vcr()
    def test_hottest_response_is_array(self) -> None:
        """Response is a JSON array of stories (not wrapped in an object)."""
        with create_http_client() as client:
            resp = client.get("https://lobste.rs/hottest.json")
            resp.raise_for_status()

        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    @pytest.mark.vcr()
    def test_story_fields(self) -> None:
        """Each story has the fields our collector extracts."""
        with create_http_client() as client:
            resp = client.get("https://lobste.rs/hottest.json")
            resp.raise_for_status()

        story = resp.json()[0]
        assert "short_id" in story
        assert "title" in story
        assert "url" in story
        assert "score" in story
        assert "comment_count" in story
        assert "tags" in story
        assert isinstance(story["tags"], list)
        assert "submitter_user" in story
        assert "created_at" in story
        assert "comments_url" in story

    @pytest.mark.vcr()
    def test_short_id_is_string(self) -> None:
        """``short_id`` is a string (our collector uses it as external_id)."""
        with create_http_client() as client:
            resp = client.get("https://lobste.rs/hottest.json")
            resp.raise_for_status()

        story = resp.json()[0]
        assert isinstance(story["short_id"], str)
        assert len(story["short_id"]) > 0
