"""Tests for the Reddit collector."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest
import sqlalchemy as sa

from aggre.collectors.reddit.collector import RedditCollector, _rate_limit_sleep
from aggre.collectors.reddit.config import RedditConfig, RedditSource
from aggre.db import SilverDiscussion
from aggre.utils.http import create_http_client
from tests.factories import (
    make_config,
    reddit_comment,
    reddit_comment_listing,
    reddit_listing,
    reddit_post,
    seed_content,
    seed_discussion,
)
from tests.helpers import collect, get_discussions, get_sources

pytestmark = pytest.mark.integration


class TestRedditCollectorDiscussions:
    def test_stores_posts_in_raw_and_content(self, engine, mock_http):
        post = reddit_post()
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            count = collect(RedditCollector(), engine, config.reddit, config.settings)

        assert count == 1

        items = get_discussions(engine)
        assert len(items) == 1
        assert items[0].title == "Test Post"
        assert items[0].author == "testuser"
        assert items[0].source_type == "reddit"
        assert "reddit.com" in items[0].url
        assert items[0].content_text == "This is the body text"

        assert items[0].score == 42
        assert items[0].comment_count == 5
        assert items[0].comments_json is None  # pending: no comments fetched yet

        meta = json.loads(items[0].meta)
        assert meta["subreddit"] == "python"
        assert meta["flair"] == "Discussion"

    def test_dedup_same_post_in_hot_and_new(self, engine, mock_http):
        post = reddit_post()
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            count = collect(RedditCollector(), engine, config.reddit, config.settings)

        assert count == 1

        assert len(get_discussions(engine)) == 1

    def test_multiple_unique_posts(self, engine, mock_http):
        post1 = reddit_post(post_id="aaa", title="First")
        post2 = reddit_post(post_id="bbb", title="Second")
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=reddit_listing(post1))
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=reddit_listing(post2))

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            count = collect(RedditCollector(), engine, config.reddit, config.settings)

        assert count == 2

        assert len(get_discussions(engine)) == 2

    def test_collect_does_not_fetch_comments(self, engine, mock_http):
        """collect_discussions() should only make listing requests, not comment requests."""
        post = reddit_post()
        listing = reddit_listing(post)

        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)
        # Catch-all for comment URLs — should never be called
        comment_route = mock_http.get(url__regex=r".*/comments/.*\.json.*").respond(json=[])

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings)

        # No comment URLs should have been requested
        assert not comment_route.called

        # But comments should be pending (comments_json not yet fetched)
        items = get_discussions(engine)
        assert len(items) == 1
        assert items[0].comments_json is None


class TestRedditCollectorFetchDiscussionComments:
    def test_sets_comments_fetched_at_on_success(self, engine, mock_http):
        config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
        collector = RedditCollector()

        content_id = seed_content(engine, "https://example.com/reddit-fetch-test", domain="example.com")
        discussion_id = seed_discussion(
            engine,
            source_type="reddit",
            external_id="t3_abc123",
            content_id=content_id,
            meta='{"subreddit": "python"}',
        )

        comment = reddit_comment(comment_id="com1", body="Great post!")
        comment_response = reddit_comment_listing(comment)
        mock_http.get(url__regex=r".*/comments/abc123\.json.*").respond(json=comment_response)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            collector.fetch_discussion_comments(
                engine, discussion_id, "t3_abc123", '{"subreddit": "python"}', config.settings
            )

        with engine.connect() as conn:
            row = conn.execute(
                sa.select(SilverDiscussion.comments_fetched_at).where(SilverDiscussion.id == discussion_id)
            ).first()
        assert row.comments_fetched_at is not None


class TestRedditCollectorRateLimit:
    def test_sleep_called_before_each_listing_request(self, engine):
        """Verify rate-limit sleep fires before each listing HTTP request."""
        config = make_config(
            reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
            rate_limit=2.0,
        )
        collector = RedditCollector()

        post1 = reddit_post(post_id="aaa", title="First")
        hot_listing = reddit_listing(post1)

        call_order: list[str] = []

        def fake_get(url):
            call_order.append("get")
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            if "hot.json" in url:
                resp.json.return_value = hot_listing
            else:
                resp.json.return_value = reddit_listing()
            return resp

        def fake_sleep(seconds):
            if seconds == 2.0:
                call_order.append("sleep")

        with (
            patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls,
            patch("aggre.collectors.reddit.collector.time.sleep", side_effect=fake_sleep),
        ):
            client_instance = MagicMock()
            client_instance.__enter__ = MagicMock(return_value=client_instance)
            client_instance.__exit__ = MagicMock(return_value=False)
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            collect(collector, engine, config.reddit, config.settings)

        # Expect: sleep,get (hot), sleep,get (new) = 2 pairs
        # Every "get" must be immediately preceded by "sleep"
        for i, entry in enumerate(call_order):
            if entry == "get":
                assert i > 0 and call_order[i - 1] == "sleep", (
                    f"HTTP request at index {i} was not preceded by rate-limit sleep. Full sequence: {call_order}"
                )


class TestAdaptiveRateLimit:
    def test_exhausted_rate_limit_sleeps_for_reset(self, caplog):
        """When remaining <= 1, sleep for the full reset duration."""
        resp = MagicMock()
        resp.headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "30"}

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            with caplog.at_level(logging.WARNING, logger="aggre.collectors.reddit.collector"):
                _rate_limit_sleep(resp, 1.0)

        mock_sleep.assert_called_once_with(30.0)
        assert any("rate_limit_exhausted" in r.message for r in caplog.records)

    def test_low_rate_limit_sleeps_proportionally(self, caplog):
        """When remaining < 5, sleep for reset/remaining."""
        resp = MagicMock()
        resp.headers = {"x-ratelimit-remaining": "3", "x-ratelimit-reset": "30"}

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            with caplog.at_level(logging.INFO, logger="aggre.collectors.reddit.collector"):
                _rate_limit_sleep(resp, 1.0)

        mock_sleep.assert_called_once_with(10.0)  # 30 / 3
        assert any("rate_limit_low" in r.message for r in caplog.records)

    def test_healthy_rate_limit_sleeps_min_delay(self):
        """When remaining >= 5, sleep for min_delay."""
        resp = MagicMock()
        resp.headers = {"x-ratelimit-remaining": "50", "x-ratelimit-reset": "60"}

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 2.0)

        mock_sleep.assert_called_once_with(2.0)

    def test_missing_headers_sleeps_min_delay(self):
        """When rate-limit headers are absent, fall back to min_delay."""
        resp = MagicMock()
        resp.headers = {}

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 3.0)

        mock_sleep.assert_called_once_with(3.0)


class TestRetryAfter429:
    def test_fetch_json_sleeps_on_retry_after(self, caplog):
        """_fetch_json should sleep on 429 with Retry-After before raising."""
        from aggre.collectors.reddit.collector import _fetch_json

        client = MagicMock()

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"retry-after": "5"}
        resp_429.raise_for_status.side_effect = httpx.HTTPStatusError("429", request=MagicMock(), response=resp_429)

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.headers = {}
        resp_ok.json.return_value = {"data": "ok"}

        client.get.side_effect = [resp_429, resp_ok]

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            with caplog.at_level(logging.WARNING, logger="aggre.collectors.reddit.collector"):
                data, resp = _fetch_json(client, "http://example.com")

        assert data == {"data": "ok"}
        assert any("429_retry_after" in r.message for r in caplog.records)


class TestRedditCollectorSources:
    def test_creates_source_row(self, engine, mock_http):
        listing = reddit_listing()
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings)

        rows = get_sources(engine)
        assert len(rows) == 1
        assert rows[0].type == "reddit"
        assert rows[0].name == "python"
        src_config = json.loads(rows[0].config)
        assert src_config["subreddit"] == "python"
        assert rows[0].last_fetched_at is not None

    def test_reuses_existing_source(self, engine, mock_http):
        listing = reddit_listing()
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings)
            collect(RedditCollector(), engine, config.reddit, config.settings)

        assert len(get_sources(engine)) == 1


class TestRedditCollectorProxy:
    def test_collect_passes_proxy_url_to_http_client(self, engine, mock_http):
        """collect_discussions() should pass proxy_url from config to create_http_client."""
        listing = reddit_listing()
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with (
            patch(
                "aggre.collectors.reddit.collector.create_http_client",
                wraps=create_http_client,
            ) as mock_factory,
            patch("aggre.collectors.reddit.collector.time.sleep"),
        ):
            config = make_config(
                reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
                proxy_url="socks5://tor-proxy:9150",
            )
            collect(RedditCollector(), engine, config.reddit, config.settings)

        mock_factory.assert_called_with(proxy_url="socks5://tor-proxy:9150")

    def test_collect_passes_none_when_proxy_empty(self, engine, mock_http):
        """collect_discussions() should pass proxy_url=None when config has empty proxy_url."""
        listing = reddit_listing()
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with (
            patch(
                "aggre.collectors.reddit.collector.create_http_client",
                wraps=create_http_client,
            ) as mock_factory,
            patch("aggre.collectors.reddit.collector.time.sleep"),
        ):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings)

        mock_factory.assert_called_with(proxy_url=None)
