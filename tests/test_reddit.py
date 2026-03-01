"""Tests for the Reddit collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
import sqlalchemy as sa

from aggre.collectors.reddit.collector import RedditCollector, _rate_limit_sleep
from aggre.collectors.reddit.config import RedditConfig, RedditSource
from aggre.db import SilverObservation, Source
from aggre.utils.http import create_http_client
from tests.factories import (
    make_config,
    reddit_comment,
    reddit_comment_listing,
    reddit_listing,
    reddit_post,
)
from tests.helpers import collect

pytestmark = pytest.mark.integration


class TestRedditCollectorDiscussions:
    def test_stores_posts_in_raw_and_content(self, engine, mock_http, log):
        post = reddit_post()
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            count = collect(RedditCollector(), engine, config.reddit, config.settings, log)

        assert count == 1

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverObservation)).fetchall()
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

    def test_dedup_same_post_in_hot_and_new(self, engine, mock_http, log):
        post = reddit_post()
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            count = collect(RedditCollector(), engine, config.reddit, config.settings, log)

        assert count == 1

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverObservation)).fetchall()
            assert len(items) == 1

    def test_multiple_unique_posts(self, engine, mock_http, log):
        post1 = reddit_post(post_id="aaa", title="First")
        post2 = reddit_post(post_id="bbb", title="Second")
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=reddit_listing(post1))
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=reddit_listing(post2))

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            count = collect(RedditCollector(), engine, config.reddit, config.settings, log)

        assert count == 2

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverObservation)).fetchall()
            assert len(items) == 2

    def test_collect_does_not_fetch_comments(self, engine, mock_http, log):
        """collect_references() should only make listing requests, not comment requests."""
        post = reddit_post()
        listing = reddit_listing(post)

        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)
        # Catch-all for comment URLs — should never be called
        comment_route = mock_http.get(url__regex=r".*/comments/.*\.json.*").respond(json=[])

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        # No comment URLs should have been requested
        assert not comment_route.called

        # But comments should be pending (comments_json not yet fetched)
        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverObservation)).fetchall()
            assert len(items) == 1
            assert items[0].comments_json is None


class TestRedditCollectorComments:
    def test_collect_comments_fetches_and_marks_done(self, engine, mock_http, log):
        """collect_comments() should fetch comments for pending posts and mark them done."""
        # First, collect posts
        post = reddit_post()
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        # Reset mock_http for comment requests
        mock_http.reset()

        # Now collect comments
        comment = reddit_comment(comment_id="com1", body="Great post!")
        comment_response = reddit_comment_listing(comment)
        mock_http.get(url__regex=r".*/comments/abc123\.json.*").respond(json=comment_response)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            fetched = RedditCollector().collect_comments(engine, config.reddit, config.settings, log, batch_limit=10)

        assert fetched == 1

        with engine.connect() as conn:
            # Verify comments are stored as JSON on SilverObservation
            items = conn.execute(sa.select(SilverObservation)).fetchall()
            assert len(items) == 1
            assert items[0].comments_json is not None
            comments_data = json.loads(items[0].comments_json)
            assert len(comments_data) == 1
            assert comments_data[0]["data"]["body"] == "Great post!"
            assert comments_data[0]["data"]["author"] == "commenter"
            assert items[0].comment_count == 1

            # Comments have been fetched
            assert items[0].comments_json is not None

    def test_collect_comments_respects_batch_limit(self, engine, mock_http, log):
        """collect_comments() should only process batch_limit posts."""
        # Create 3 posts
        post1 = reddit_post(post_id="aaa", title="First")
        post2 = reddit_post(post_id="bbb", title="Second")
        post3 = reddit_post(post_id="ccc", title="Third")
        listing = reddit_listing(post1, post2, post3)

        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=reddit_listing())

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        # Reset mock_http for comment requests
        mock_http.reset()

        # Fetch comments with batch_limit=2
        mock_http.get(url__regex=r".*/comments/aaa\.json.*").respond(json=reddit_comment_listing())
        mock_http.get(url__regex=r".*/comments/bbb\.json.*").respond(json=reddit_comment_listing())
        mock_http.get(url__regex=r".*/comments/ccc\.json.*").respond(json=reddit_comment_listing())

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            fetched = RedditCollector().collect_comments(engine, config.reddit, config.settings, log, batch_limit=2)

        assert fetched == 2

        # One should still be pending
        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverObservation)).fetchall()
            done = [i for i in items if i.comments_json is not None]
            pending = [i for i in items if i.comments_json is None]
            assert len(done) == 2
            assert len(pending) == 1

    def test_collect_comments_no_pending(self, engine, log):
        """collect_comments() returns 0 when no pending posts exist."""
        config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
        fetched = RedditCollector().collect_comments(engine, config.reddit, config.settings, log, batch_limit=10)
        assert fetched == 0

    def test_collect_comments_zero_batch_limit(self, engine, log):
        """collect_comments() returns 0 when batch_limit is 0."""
        config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
        fetched = RedditCollector().collect_comments(engine, config.reddit, config.settings, log, batch_limit=0)
        assert fetched == 0

    def test_nested_comments(self, engine, mock_http, log):
        # Collect posts first
        post = reddit_post()
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        # Reset mock_http for comment requests
        mock_http.reset()

        # Fetch comments with nested replies
        reply = reddit_comment(comment_id="reply1", body="I agree", parent_id="t1_com1")
        parent_comment = reddit_comment(
            comment_id="com1",
            body="Top level",
            replies={"data": {"children": [reply]}},
        )
        comment_response = reddit_comment_listing(parent_comment)
        mock_http.get(url__regex=r".*/comments/abc123\.json.*").respond(json=comment_response)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            RedditCollector().collect_comments(engine, config.reddit, config.settings, log, batch_limit=10)

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverObservation)).fetchall()
            assert items[0].comments_json is not None
            comments_data = json.loads(items[0].comments_json)
            # The top-level children list has 1 comment (parent_comment)
            assert len(comments_data) == 1
            assert comments_data[0]["data"]["body"] == "Top level"
            # The nested reply is inside the parent comment's replies
            replies = comments_data[0]["data"]["replies"]["data"]["children"]
            assert len(replies) == 1
            assert replies[0]["data"]["body"] == "I agree"


class TestRedditCollectorRateLimit:
    def test_sleep_called_before_each_listing_request(self, engine):
        """Verify rate-limit sleep fires before each listing HTTP request."""
        config = make_config(
            reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
            rate_limit=2.0,
        )
        log = MagicMock()
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

            collect(collector, engine, config.reddit, config.settings, log)

        # Expect: sleep,get (hot), sleep,get (new) = 2 pairs
        # Every "get" must be immediately preceded by "sleep"
        for i, entry in enumerate(call_order):
            if entry == "get":
                assert i > 0 and call_order[i - 1] == "sleep", (
                    f"HTTP request at index {i} was not preceded by rate-limit sleep. Full sequence: {call_order}"
                )


class TestAdaptiveRateLimit:
    def test_exhausted_rate_limit_sleeps_for_reset(self):
        """When remaining <= 1, sleep for the full reset duration."""
        log = MagicMock()
        resp = MagicMock()
        resp.headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "30"}

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 1.0, log)

        mock_sleep.assert_called_once_with(30.0)
        log.warning.assert_called_once()

    def test_low_rate_limit_sleeps_proportionally(self):
        """When remaining < 5, sleep for reset/remaining."""
        log = MagicMock()
        resp = MagicMock()
        resp.headers = {"x-ratelimit-remaining": "3", "x-ratelimit-reset": "30"}

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 1.0, log)

        mock_sleep.assert_called_once_with(10.0)  # 30 / 3
        log.info.assert_called_once()

    def test_healthy_rate_limit_sleeps_min_delay(self):
        """When remaining >= 5, sleep for min_delay."""
        log = MagicMock()
        resp = MagicMock()
        resp.headers = {"x-ratelimit-remaining": "50", "x-ratelimit-reset": "60"}

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 2.0, log)

        mock_sleep.assert_called_once_with(2.0)

    def test_missing_headers_sleeps_min_delay(self):
        """When rate-limit headers are absent, fall back to min_delay."""
        log = MagicMock()
        resp = MagicMock()
        resp.headers = {}

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 3.0, log)

        mock_sleep.assert_called_once_with(3.0)


class TestRetryAfter429:
    def test_fetch_json_sleeps_on_retry_after(self):
        """_fetch_json should sleep on 429 with Retry-After before raising."""
        from aggre.collectors.reddit.collector import _fetch_json

        log = MagicMock()
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
            # Need to call through tenacity — it will retry after 429
            data, resp = _fetch_json(client, "http://example.com", log)

        assert data == {"data": "ok"}
        log.warning.assert_called_once()


class TestRedditCollectorSources:
    def test_creates_source_row(self, engine, mock_http, log):
        listing = reddit_listing()
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "reddit"
            assert rows[0].name == "python"
            src_config = json.loads(rows[0].config)
            assert src_config["subreddit"] == "python"
            assert rows[0].last_fetched_at is not None

    def test_reuses_existing_source(self, engine, mock_http, log):
        listing = reddit_listing()
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))
            collect(RedditCollector(), engine, config.reddit, config.settings, log)
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1


class TestRedditCollectorProxy:
    def test_collect_passes_proxy_url_to_http_client(self, engine, mock_http, log):
        """collect_references() should pass proxy_url from config to create_http_client."""
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
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        mock_factory.assert_called_with(proxy_url="socks5://tor-proxy:9150")

    def test_collect_passes_none_when_proxy_empty(self, engine, mock_http, log):
        """collect_references() should pass proxy_url=None when config has empty proxy_url."""
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
            collect(RedditCollector(), engine, config.reddit, config.settings, log)

        mock_factory.assert_called_with(proxy_url=None)
