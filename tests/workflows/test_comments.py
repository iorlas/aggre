"""Tests for per-item comment fetching (fetch_one_comments).

Uses real PostgreSQL engine for DB queries, mocks collectors.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aggre.workflows.comments import _resolve_proxy, fetch_one_comments
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

        assert result.status == "skipped"
        assert result.reason == "no_collector"

    def test_returns_not_found_for_missing_discussion(self, engine):
        settings = make_config().settings

        result = fetch_one_comments(engine, 99999, "hackernews", settings)

        assert result.status == "skipped"
        assert result.reason == "not_found"

    def test_returns_already_done_when_comments_exist(self, engine):
        settings = make_config().settings
        disc_id = _seed_discussion_for_comments(engine, comments_json='[{"id": 1}]')

        with patch("aggre.workflows.comments.COLLECTORS", {"hackernews": MagicMock()}):
            result = fetch_one_comments(engine, disc_id, "hackernews", settings)

        assert result.status == "skipped"
        assert result.reason == "already_done"

    @patch("aggre.workflows.comments.COLLECTORS")
    def test_fetches_comments_successfully(self, mock_collectors, engine):
        settings = make_config().settings
        disc_id = _seed_discussion_for_comments(engine, external_id="hn001", meta='{"some": "data"}')

        mock_cls = MagicMock()
        mock_collectors.get.return_value = mock_cls

        result = fetch_one_comments(engine, disc_id, "hackernews", settings)

        assert result.status == "fetched"
        mock_cls.return_value.fetch_discussion_comments.assert_called_once_with(
            engine,
            disc_id,
            "hn001",
            '{"some": "data"}',
            settings,
            proxy_url=None,
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

        assert result.status == "fetched"
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

        assert result.status == "fetched"
        mock_cls.return_value.fetch_discussion_comments.assert_called_once()

    @patch("aggre.workflows.comments.COLLECTORS")
    @patch("aggre.workflows.comments.get_proxy", return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"})
    def test_reddit_uses_proxy_rotation(self, mock_get_proxy, mock_collectors, engine):
        """Reddit comment fetching uses proxy API for per-request IP rotation."""
        settings = make_config().settings
        settings.proxy_api_url = "http://proxy-api:8080"
        disc_id = _seed_discussion_for_comments(
            engine,
            source_type="reddit",
            external_id="t3_proxy",
        )

        mock_cls = MagicMock()
        mock_collectors.get.return_value = mock_cls

        result = fetch_one_comments(engine, disc_id, "reddit", settings)

        assert result.status == "fetched"
        mock_get_proxy.assert_called_once_with("http://proxy-api:8080", protocol="socks5")
        mock_cls.return_value.fetch_discussion_comments.assert_called_once_with(
            engine,
            disc_id,
            "t3_proxy",
            None,
            settings,
            proxy_url="socks5://1.2.3.4:1080",
        )

    @patch("aggre.workflows.comments.COLLECTORS")
    @patch("aggre.workflows.comments.report_failure")
    @patch("aggre.workflows.comments.get_proxy", return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"})
    def test_reddit_reports_proxy_failure_on_error(self, mock_get_proxy, mock_report, mock_collectors, engine):
        """Proxy failure is reported when Reddit comment fetch raises."""
        settings = make_config().settings
        settings.proxy_api_url = "http://proxy-api:8080"
        disc_id = _seed_discussion_for_comments(
            engine,
            source_type="reddit",
            external_id="t3_fail",
        )

        mock_cls = MagicMock()
        mock_cls.return_value.fetch_discussion_comments.side_effect = RuntimeError("connection refused")
        mock_collectors.get.return_value = mock_cls

        with pytest.raises(RuntimeError, match="connection refused"):
            fetch_one_comments(engine, disc_id, "reddit", settings)

        mock_report.assert_called_once_with("http://proxy-api:8080", "1.2.3.4:1080")

    @patch("aggre.workflows.comments.COLLECTORS")
    @patch("aggre.workflows.comments.get_proxy", return_value=None)
    def test_reddit_falls_back_to_static_proxy(self, mock_get_proxy, mock_collectors, engine):
        """Falls back to static proxy_url when proxy API returns None."""
        settings = make_config().settings
        settings.proxy_api_url = "http://proxy-api:8080"
        settings.proxy_url = "socks5://static:1080"
        disc_id = _seed_discussion_for_comments(
            engine,
            source_type="reddit",
            external_id="t3_fallback",
        )

        mock_cls = MagicMock()
        mock_collectors.get.return_value = mock_cls

        result = fetch_one_comments(engine, disc_id, "reddit", settings)

        assert result.status == "fetched"
        # Falls back to static proxy
        mock_cls.return_value.fetch_discussion_comments.assert_called_once_with(
            engine,
            disc_id,
            "t3_fallback",
            None,
            settings,
            proxy_url="socks5://static:1080",
        )


class TestResolveProxy:
    def test_returns_proxy_api_for_reddit(self):
        settings = make_config().settings
        settings.proxy_api_url = "http://proxy-api:8080"

        with patch("aggre.workflows.comments.get_proxy", return_value={"addr": "5.6.7.8:1080", "protocol": "socks5"}) as mock:
            url, addr = _resolve_proxy("reddit", settings)

        assert url == "socks5://5.6.7.8:1080"
        assert addr == "5.6.7.8:1080"
        mock.assert_called_once_with("http://proxy-api:8080", protocol="socks5")

    def test_returns_static_proxy_for_hackernews(self):
        settings = make_config().settings
        settings.proxy_api_url = "http://proxy-api:8080"
        settings.proxy_url = "socks5://static:1080"

        url, addr = _resolve_proxy("hackernews", settings)

        assert url == "socks5://static:1080"
        assert addr == ""

    def test_returns_empty_when_no_proxy_configured(self):
        settings = make_config().settings

        url, addr = _resolve_proxy("reddit", settings)

        assert url == ""
        assert addr == ""
