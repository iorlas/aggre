"""Tests for the Hacker News collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource
from aggre.db import SilverContent, SilverDiscussion
from tests.factories import (
    hn_comment_child,
    hn_hit,
    hn_item_response,
    hn_search_response,
    make_config,
    seed_content,
    seed_discussion,
)
from tests.helpers import collect, get_discussions, get_sources

pytestmark = pytest.mark.integration


class TestHackernewsCollectorDiscussions:
    def test_stores_posts(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            count = collect(collector, engine, config.hackernews, config.settings)

        assert count == 1

        items = get_discussions(engine)
        assert len(items) == 1
        assert items[0].title == "Test Story"
        assert items[0].author == "pg"
        assert items[0].source_type == "hackernews"
        assert items[0].url == "https://example.com/article"

        assert items[0].score == 100
        assert items[0].comment_count == 25
        assert items[0].comments_json is None  # pending: no comments fetched yet

        meta = json.loads(items[0].meta)
        assert "hn_url" in meta

    def test_dedup_same_story(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            count1 = collect(collector, engine, config.hackernews, config.settings)
            count2 = collect(collector, engine, config.hackernews, config.settings)

        assert count1 == 1
        assert count2 == 1  # collect_discussions returns refs regardless; dedup is in upsert

        assert len(get_discussions(engine)) == 1

    def test_multiple_stories(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit1 = hn_hit(object_id="111", title="First")
        hit2 = hn_hit(object_id="222", title="Second")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit1, hit2),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            count = collect(collector, engine, config.hackernews, config.settings)

        assert count == 2

    def test_story_without_url_creates_self_post_content(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit(object_id="999", url=None, story_text="This is a self-post with some text content.")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        with engine.connect() as conn:
            item = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert item.url == "https://news.ycombinator.com/item?id=999"
            # Self-posts now create SilverContent with text populated
            assert item.content_id is not None

            content = conn.execute(sa.select(SilverContent).where(SilverContent.id == item.content_id)).fetchone()
            assert content is not None
            assert content.text == "This is a self-post with some text content."

    def test_no_config_returns_zero(self, engine):
        config = make_config(
            hackernews=HackernewsConfig(sources=[]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()
        assert collect(collector, engine, config.hackernews, config.settings) == 0

    def test_http_fetch_failure_continues(self, engine, mock_http):
        """API fetch fails → logs exception, continues to next source."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").mock(
            side_effect=Exception("Connection refused"),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            count = collect(collector, engine, config.hackernews, config.settings)

        assert count == 0

    def test_empty_object_id_skipped(self, engine, mock_http):
        """Hit with empty objectID → skipped."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit(object_id="")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            count = collect(collector, engine, config.hackernews, config.settings)

        assert count == 0
        assert len(get_discussions(engine)) == 0

    def test_fetches_all_stories_not_just_front_page(self, engine, mock_http):
        """Collector uses tags=story (not story,front_page) to catch all stories."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit()
        route = mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        # Verify the query used tags=story (not story,front_page)
        request = route.calls[0].request
        assert "tags=story" in str(request.url)
        assert "front_page" not in str(request.url)


class TestHackernewsCollectorFetchDiscussionComments:
    def test_sets_comments_fetched_at_on_success(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        content_id = seed_content(engine, "https://example.com/hn-fetch-test", domain="example.com")
        discussion_id = seed_discussion(engine, source_type="hackernews", external_id="12345", content_id=content_id)

        comment = hn_comment_child(comment_id=100, text="Nice!")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/items/12345").respond(
            json=hn_item_response(object_id="12345", children=[comment]),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collector.fetch_discussion_comments(engine, discussion_id, "12345", None, config.settings)

        with engine.connect() as conn:
            row = conn.execute(sa.select(SilverDiscussion.comments_fetched_at).where(SilverDiscussion.id == discussion_id)).first()
        assert row.comments_fetched_at is not None


class TestHackernewsSource:
    def test_creates_source_row(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "hackernews"
        assert rows[0].name == "Hacker News"

    def test_reuses_existing_source(self, engine, mock_http):
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)
            collect(collector, engine, config.hackernews, config.settings)

        assert len(get_sources(engine)) == 1


class TestBaseCollectorEdgeCases:
    """Test BaseCollector helper methods via HackernewsCollector."""

    def test_is_source_recent_returns_true(self, engine, mock_http):
        """Source fetched recently → _is_source_recent returns True."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        source_id = get_sources(engine)[0].id
        assert collector._is_source_recent(engine, source_id, ttl_minutes=60) is True

    def test_is_source_recent_returns_false_when_disabled(self, engine, mock_http):
        """ttl_minutes=0 → always returns False (TTL disabled)."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        source_id = get_sources(engine)[0].id
        assert collector._is_source_recent(engine, source_id, ttl_minutes=0) is False

    def test_upsert_discussion_do_nothing_on_conflict(self, engine, mock_http):
        """_upsert_discussion with update_columns=None → on_conflict_do_nothing."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
        )
        collector = HackernewsCollector()

        hit = hn_hit(object_id="99", title="Original Title")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(collector, engine, config.hackernews, config.settings)

        # Now manually upsert with update_columns=None — should NOT update title
        with engine.begin() as conn:
            collector._upsert_discussion(
                conn,
                {
                    "source_type": "hackernews",
                    "external_id": "99",
                    "title": "Updated Title",
                    "source_id": get_sources(engine)[0].id,
                },
                update_columns=None,
            )

        items = get_discussions(engine)
        assert items[0].title == "Original Title"  # on_conflict_do_nothing

    def test_ensure_self_post_content_existing(self, engine):
        """_ensure_self_post_content when content already exists → returns existing id."""
        collector = HackernewsCollector()

        hn_url = "https://news.ycombinator.com/item?id=12345"

        with engine.begin() as conn:
            id1 = collector._ensure_self_post_content(conn, hn_url, "First text")
            id2 = collector._ensure_self_post_content(conn, hn_url, "Different text")

        assert id1 is not None
        assert id1 == id2  # Returns existing content id

    def test_ensure_self_post_content_empty_text(self, engine):
        """_ensure_self_post_content with empty text → returns None."""
        collector = HackernewsCollector()

        with engine.begin() as conn:
            result = collector._ensure_self_post_content(conn, "https://news.ycombinator.com/item?id=12345", "")

        assert result is None


class TestHackernewsCollectorProxy:
    def test_collect_calls_get_proxy_once(self, engine, mock_http):
        """collect_discussions() should call get_proxy() once (per-run)."""
        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with (
            patch(
                "aggre.collectors.hackernews.collector.get_proxy", return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"}
            ) as mock_gp,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            config = make_config(
                hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
                rate_limit=0.0,
                proxy_api_url="http://proxy-hub:8000",
            )
            count = collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        assert count == 1
        mock_gp.assert_called_once_with("http://proxy-hub:8000", protocol="socks5")

    def test_collect_no_proxy_when_api_url_empty(self, engine, mock_http):
        """collect_discussions() should not call get_proxy() when proxy_api_url is empty."""
        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with (
            patch("aggre.collectors.hackernews.collector.get_proxy") as mock_gp,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            config = make_config(
                hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
                rate_limit=0.0,
            )
            collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        mock_gp.assert_not_called()

    def test_collect_proceeds_when_get_proxy_returns_none(self, engine, mock_http):
        """collect_discussions() should proceed without proxy when get_proxy() returns None."""
        hit = hn_hit()
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(
            json=hn_search_response(hit),
        )

        with (
            patch("aggre.collectors.hackernews.collector.get_proxy", return_value=None),
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            config = make_config(
                hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
                rate_limit=0.0,
                proxy_api_url="http://proxy-hub:8000",
            )
            count = collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        assert count == 1

    def test_fetch_comments_calls_get_proxy(self, engine, mock_http):
        """fetch_discussion_comments() should call get_proxy() internally."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
            proxy_api_url="http://proxy-hub:8000",
        )
        collector = HackernewsCollector()

        content_id = seed_content(engine, "https://example.com/hn-proxy-test", domain="example.com")
        discussion_id = seed_discussion(engine, source_type="hackernews", external_id="99999", content_id=content_id)

        comment = hn_comment_child(comment_id=100, text="Nice!")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/items/99999").respond(
            json=hn_item_response(object_id="99999", children=[comment]),
        )

        with (
            patch(
                "aggre.collectors.hackernews.collector.get_proxy",
                return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"},
            ) as mock_gp,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            collector.fetch_discussion_comments(engine, discussion_id, "99999", None, config.settings)

        mock_gp.assert_called_once_with("http://proxy-hub:8000", protocol="socks5")

    def test_fetch_comments_reports_failure_on_error(self, engine):
        """fetch_discussion_comments() should call report_failure() on error with proxy."""
        config = make_config(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="Hacker News")]),
            rate_limit=0.0,
            proxy_api_url="http://proxy-hub:8000",
        )
        collector = HackernewsCollector()

        content_id = seed_content(engine, "https://example.com/hn-fail-test", domain="example.com")
        discussion_id = seed_discussion(engine, source_type="hackernews", external_id="88888", content_id=content_id)

        with (
            patch(
                "aggre.collectors.hackernews.collector.get_proxy",
                return_value={"addr": "1.2.3.4:1080", "protocol": "socks5"},
            ),
            patch("aggre.collectors.hackernews.collector.report_failure") as mock_rf,
            patch("aggre.collectors.hackernews.collector.create_http_client") as mock_client_cls,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            client_instance = MagicMock()
            client_instance.__enter__ = MagicMock(return_value=client_instance)
            client_instance.__exit__ = MagicMock(return_value=False)
            client_instance.get.side_effect = Exception("connection failed")
            mock_client_cls.return_value = client_instance

            with pytest.raises(Exception, match="connection failed"):
                collector.fetch_discussion_comments(engine, discussion_id, "88888", None, config.settings)

        mock_rf.assert_called_once_with("http://proxy-hub:8000", "1.2.3.4:1080")
