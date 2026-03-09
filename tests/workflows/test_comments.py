"""Tests for per-item comment fetching (fetch_one_comments).

Uses real PostgreSQL engine for DB queries, mocks collectors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aggre.workflows.comments import fetch_one_comments
from tests.factories import make_config, seed_content, seed_discussion

pytestmark = pytest.mark.integration


def _seed_discussion_for_comments(
    engine,
    *,
    source_type: str = "hackernews",
    external_id: str = "12345",
    comments_json: str | None = None,
    meta: str | None = None,
) -> int:
    """Seed content + discussion, return discussion_id."""
    content_id = seed_content(engine, f"https://example.com/{external_id}", domain="example.com")
    return seed_discussion(
        engine,
        source_type=source_type,
        external_id=external_id,
        content_id=content_id,
        comments_json=comments_json,
        meta=meta,
    )


class TestFetchOneComments:
    def test_returns_no_collector_for_unknown_source(self, engine):
        settings = make_config().settings
        disc_id = _seed_discussion_for_comments(engine)

        result = fetch_one_comments(engine, disc_id, "unknown_source", settings)

        assert result == "no_collector"

    def test_returns_not_found_for_missing_discussion(self, engine):
        settings = make_config().settings

        result = fetch_one_comments(engine, 99999, "hackernews", settings)

        assert result == "not_found"

    def test_returns_already_done_when_comments_exist(self, engine):
        settings = make_config().settings
        disc_id = _seed_discussion_for_comments(engine, comments_json='[{"id": 1}]')

        with patch("aggre.workflows.comments.COLLECTORS", {"hackernews": MagicMock()}):
            result = fetch_one_comments(engine, disc_id, "hackernews", settings)

        assert result == "already_done"

    @patch("aggre.workflows.comments.COLLECTORS")
    def test_fetches_comments_successfully(self, mock_collectors, engine):
        settings = make_config().settings
        disc_id = _seed_discussion_for_comments(engine, external_id="hn001", meta='{"some": "data"}')

        mock_cls = MagicMock()
        mock_collectors.get.return_value = mock_cls

        result = fetch_one_comments(engine, disc_id, "hackernews", settings)

        assert result == "fetched"
        mock_cls.return_value.fetch_discussion_comments.assert_called_once_with(
            engine,
            disc_id,
            "hn001",
            '{"some": "data"}',
            settings,
        )

    @patch("aggre.workflows.comments.COLLECTORS")
    def test_propagates_collector_error(self, mock_collectors, engine):
        """Errors from collector propagate (Hatchet handles retry)."""
        settings = make_config().settings
        disc_id = _seed_discussion_for_comments(engine, external_id="fail01")

        mock_cls = MagicMock()
        mock_cls.return_value.fetch_discussion_comments.side_effect = RuntimeError("API down")
        mock_collectors.get.return_value = mock_cls

        with pytest.raises(RuntimeError, match="API down"):
            fetch_one_comments(engine, disc_id, "hackernews", settings)

    @patch("aggre.workflows.comments.COLLECTORS")
    def test_works_with_reddit_source(self, mock_collectors, engine):
        settings = make_config().settings
        disc_id = _seed_discussion_for_comments(
            engine,
            source_type="reddit",
            external_id="t3_abc",
        )

        mock_cls = MagicMock()
        mock_collectors.get.return_value = mock_cls

        result = fetch_one_comments(engine, disc_id, "reddit", settings)

        assert result == "fetched"
        mock_cls.return_value.fetch_discussion_comments.assert_called_once()

    @patch("aggre.workflows.comments.COLLECTORS")
    def test_works_with_lobsters_source(self, mock_collectors, engine):
        settings = make_config().settings
        disc_id = _seed_discussion_for_comments(
            engine,
            source_type="lobsters",
            external_id="lob01",
        )

        mock_cls = MagicMock()
        mock_collectors.get.return_value = mock_cls

        result = fetch_one_comments(engine, disc_id, "lobsters", settings)

        assert result == "fetched"
        mock_cls.return_value.fetch_discussion_comments.assert_called_once()
