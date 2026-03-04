"""Tests for the discussion search module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource
from aggre.dagster_defs.discussion_search.job import search_content_discussions
from aggre.tracking.ops import upsert_done
from aggre.tracking.status import Stage, StageStatus
from tests.factories import make_config, seed_content
from tests.helpers import assert_tracking

pytestmark = pytest.mark.integration


class TestDiscussionSearch:
    def test_enriches_content(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/article", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 2

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        results = search_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        assert results == {"hackernews": 2, "lobsters": 1, "processed": 1}

        mock_hn.search_by_url.assert_called_once_with("https://example.com/article", engine, config.hackernews, config.settings)
        mock_lob.search_by_url.assert_called_once_with("https://example.com/article", engine, config.lobsters, config.settings)

        # Check discussion search tracking was set
        assert_tracking(engine, "webpage", "https://example.com/article", Stage.DISCUSSION_SEARCH, StageStatus.DONE)

    def test_skips_already_searched(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/old", domain="example.com")
        upsert_done(engine, "webpage", "https://example.com/old", Stage.DISCUSSION_SEARCH)

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        results = search_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
        mock_hn.search_by_url.assert_not_called()
        mock_lob.search_by_url.assert_not_called()

    def test_respects_batch_limit(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        for i in range(5):
            seed_content(engine, f"https://example.com/{i}", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.return_value = 0

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 0

        results = search_content_discussions(
            engine,
            config,
            batch_limit=3,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        # Should only process 3
        assert mock_hn.search_by_url.call_count == 3
        assert mock_lob.search_by_url.call_count == 3
        assert results["processed"] == 3

    def test_handles_search_failure_gracefully(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://example.com/fail", domain="example.com")

        mock_hn = MagicMock()
        mock_hn.search_by_url.side_effect = Exception("HN API error")

        mock_lob = MagicMock()
        mock_lob.search_by_url.return_value = 1

        results = search_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        # HN failed but lobsters succeeded
        assert results == {"hackernews": 0, "lobsters": 1, "processed": 1}

        # Content should be marked as failed (will be retried next batch)
        assert_tracking(engine, "webpage", "https://example.com/fail", Stage.DISCUSSION_SEARCH, StageStatus.FAILED)

    def test_skips_reddit_domain_content(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        seed_content(engine, "https://reddit.com/r/python/comments/abc", domain="reddit.com")

        mock_hn = MagicMock()
        mock_lob = MagicMock()

        results = search_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )

        # Reddit URLs are filtered at SQL level — never enter the batch
        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
        mock_hn.search_by_url.assert_not_called()
        mock_lob.search_by_url.assert_not_called()

    def test_no_pending_returns_zeros(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource()]),
            lobsters=LobstersConfig(sources=[LobstersSource()]),
        )

        mock_hn = MagicMock()
        mock_lob = MagicMock()

        results = search_content_discussions(
            engine,
            config,
            batch_limit=50,
            hn_collector=mock_hn,
            lobsters_collector=mock_lob,
        )
        assert results == {"hackernews": 0, "lobsters": 0, "processed": 0}
