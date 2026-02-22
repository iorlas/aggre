"""Tests for the Reddit collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import sqlalchemy as sa

from aggre.collectors.reddit import RedditCollector
from aggre.collectors.reddit.collector import _rate_limit_sleep
from aggre.http import BROWSER_USER_AGENT
from aggre.config import AppConfig, RedditConfig, RedditSource
from aggre.settings import Settings
from aggre.db import BronzeDiscussion, SilverDiscussion, Source


def _make_config(subreddits: list[str] | None = None, rate_limit: float = 0.0) -> AppConfig:
    subs = subreddits or ["python"]
    return AppConfig(
        reddit=RedditConfig(sources=[RedditSource(subreddit=s) for s in subs]),
        settings=Settings(reddit_rate_limit=rate_limit),
    )


def _make_post(post_id: str = "abc123", title: str = "Test Post", author: str = "testuser", subreddit: str = "python"):
    return {
        "kind": "t3",
        "data": {
            "name": f"t3_{post_id}",
            "title": title,
            "author": author,
            "selftext": "This is the body text",
            "permalink": f"/r/{subreddit}/comments/{post_id}/test_post/",
            "created_utc": 1700000000.0,
            "score": 42,
            "num_comments": 5,
            "link_flair_text": "Discussion",
            "subreddit": subreddit,
        },
    }


def _make_listing(*posts):
    return {"data": {"children": list(posts)}}


def _make_comment(
    comment_id: str = "com1",
    body: str = "Nice post!",
    author: str = "commenter",
    parent_id: str = "t3_abc123",
    score: int = 10,
    replies=None,
):
    comment_data = {
        "name": f"t1_{comment_id}",
        "author": author,
        "body": body,
        "score": score,
        "parent_id": parent_id,
        "created_utc": 1700001000.0,
        "replies": replies or "",
    }
    return {"kind": "t1", "data": comment_data}


def _make_comment_listing(*comments):
    """Build the [post_listing, comments_listing] structure returned by comment endpoints."""
    post_part = {"data": {"children": [_make_post()]}}
    comment_part = {"data": {"children": list(comments)}}
    return [post_part, comment_part]


def _make_response(headers: dict | None = None):
    """Create a mock httpx.Response with optional headers."""
    resp = MagicMock()
    resp.headers = headers or {}
    return resp


def _fake_get_for_listings(mock_responses):
    """Return a fake_get that returns (data, mock_response) tuples matching _fetch_json signature."""

    def fake_get(url):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        for key, data in mock_responses.items():
            if key in url:
                resp.json.return_value = data
                return resp
        resp.json.return_value = _make_listing()
        return resp

    return fake_get


class TestRedditCollectorDiscussions:
    def test_stores_posts_in_raw_and_content(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        post = _make_post()
        listing = _make_listing(post)

        mock_responses = {
            "hot.json": listing,
            "new.json": listing,
        }

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(mock_responses)
            mock_client_cls.return_value = client_instance

            count = collector.collect(engine, config.reddit, config.settings, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(BronzeDiscussion)).fetchall()
            assert len(raws) == 1
            assert raws[0].external_id == "t3_abc123"
            assert raws[0].source_type == "reddit"

            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].title == "Test Post"
            assert items[0].author == "testuser"
            assert items[0].source_type == "reddit"
            assert "reddit.com" in items[0].url
            assert items[0].content_text == "This is the body text"

            assert items[0].score == 42
            assert items[0].comment_count == 5
            assert items[0].comments_status == "pending"

            meta = json.loads(items[0].meta)
            assert meta["subreddit"] == "python"
            assert meta["flair"] == "Discussion"

    def test_dedup_same_post_in_hot_and_new(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        post = _make_post()
        listing = _make_listing(post)

        mock_responses = {
            "hot.json": listing,
            "new.json": listing,
        }

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(mock_responses)
            mock_client_cls.return_value = client_instance

            count = collector.collect(engine, config.reddit, config.settings, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(BronzeDiscussion)).fetchall()
            assert len(raws) == 1

            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1

    def test_multiple_unique_posts(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        post1 = _make_post(post_id="aaa", title="First")
        post2 = _make_post(post_id="bbb", title="Second")
        hot_listing = _make_listing(post1)
        new_listing = _make_listing(post2)

        mock_responses = {
            "hot.json": hot_listing,
            "new.json": new_listing,
        }

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(mock_responses)
            mock_client_cls.return_value = client_instance

            count = collector.collect(engine, config.reddit, config.settings, log)

        assert count == 2

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 2

    def test_collect_does_not_fetch_comments(self, engine):
        """collect() should only make listing requests, not comment requests."""
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        post = _make_post()
        listing = _make_listing(post)
        mock_responses = {"hot.json": listing, "new.json": listing}
        requested_urls: list[str] = []

        def tracking_get(url):
            requested_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            for key, data in mock_responses.items():
                if key in url:
                    resp.json.return_value = data
                    return resp
            resp.json.return_value = _make_listing()
            return resp

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = tracking_get
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config.reddit, config.settings, log)

        # No comment URLs should have been requested
        assert not any("comments/" in url for url in requested_urls)

        # But comments should be pending
        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].comments_status == "pending"


class TestRedditCollectorComments:
    def test_collect_comments_fetches_and_marks_done(self, engine):
        """collect_comments() should fetch comments for pending posts and mark them done."""
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        # First, collect posts
        post = _make_post()
        listing = _make_listing(post)
        mock_responses = {"hot.json": listing, "new.json": listing}

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(mock_responses)
            mock_client_cls.return_value = client_instance
            collector.collect(engine, config.reddit, config.settings, log)

        # Now collect comments
        comment = _make_comment(comment_id="com1", body="Great post!")
        comment_response = _make_comment_listing(comment)

        comment_mock_responses = {"comments/abc123.json": comment_response}

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(comment_mock_responses)
            mock_client_cls.return_value = client_instance
            fetched = collector.collect_comments(engine, config.reddit, config.settings, log, batch_limit=10)

        assert fetched == 1

        with engine.connect() as conn:
            # Verify comments are stored as JSON on SilverDiscussion
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(items) == 1
            assert items[0].comments_json is not None
            comments_data = json.loads(items[0].comments_json)
            assert len(comments_data) == 1
            assert comments_data[0]["data"]["body"] == "Great post!"
            assert comments_data[0]["data"]["author"] == "commenter"
            assert items[0].comment_count == 1

            # Verify comments_status is now done (column)
            assert items[0].comments_status == "done"

    def test_collect_comments_respects_batch_limit(self, engine):
        """collect_comments() should only process batch_limit posts."""
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        # Create 3 posts
        post1 = _make_post(post_id="aaa", title="First")
        post2 = _make_post(post_id="bbb", title="Second")
        post3 = _make_post(post_id="ccc", title="Third")
        listing = _make_listing(post1, post2, post3)

        mock_responses = {"hot.json": listing, "new.json": _make_listing()}

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(mock_responses)
            mock_client_cls.return_value = client_instance
            collector.collect(engine, config.reddit, config.settings, log)

        # Fetch comments with batch_limit=2
        comment_mock_responses = {
            "comments/aaa.json": _make_comment_listing(),
            "comments/bbb.json": _make_comment_listing(),
            "comments/ccc.json": _make_comment_listing(),
        }

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(comment_mock_responses)
            mock_client_cls.return_value = client_instance
            fetched = collector.collect_comments(engine, config.reddit, config.settings, log, batch_limit=2)

        assert fetched == 2

        # One should still be pending (comments_status column)
        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
            statuses = [i.comments_status for i in items]
            assert statuses.count("done") == 2
            assert statuses.count("pending") == 1

    def test_collect_comments_no_pending(self, engine):
        """collect_comments() returns 0 when no pending posts exist."""
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        fetched = collector.collect_comments(engine, config.reddit, config.settings, log, batch_limit=10)
        assert fetched == 0

    def test_collect_comments_zero_batch_limit(self, engine):
        """collect_comments() returns 0 when batch_limit is 0."""
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        fetched = collector.collect_comments(engine, config.reddit, config.settings, log, batch_limit=0)
        assert fetched == 0

    def test_nested_comments(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        # Collect posts first
        post = _make_post()
        listing = _make_listing(post)
        mock_responses = {"hot.json": listing, "new.json": listing}

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(mock_responses)
            mock_client_cls.return_value = client_instance
            collector.collect(engine, config.reddit, config.settings, log)

        # Fetch comments with nested replies
        reply = _make_comment(comment_id="reply1", body="I agree", parent_id="t1_com1")
        parent_comment = _make_comment(
            comment_id="com1",
            body="Top level",
            replies={"data": {"children": [reply]}},
        )
        comment_response = _make_comment_listing(parent_comment)

        comment_mock_responses = {"comments/abc123.json": comment_response}

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings(comment_mock_responses)
            mock_client_cls.return_value = client_instance
            collector.collect_comments(engine, config.reddit, config.settings, log, batch_limit=10)

        with engine.connect() as conn:
            items = conn.execute(sa.select(SilverDiscussion)).fetchall()
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
        config = _make_config(rate_limit=2.0)
        log = MagicMock()
        collector = RedditCollector()

        post1 = _make_post(post_id="aaa", title="First")
        hot_listing = _make_listing(post1)

        call_order: list[str] = []

        def fake_get(url):
            call_order.append("get")
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {}
            if "hot.json" in url:
                resp.json.return_value = hot_listing
            else:
                resp.json.return_value = _make_listing()
            return resp

        def fake_sleep(seconds):
            if seconds == 2.0:
                call_order.append("sleep")

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch(
            "aggre.collectors.reddit.collector.time.sleep", side_effect=fake_sleep
        ):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config.reddit, config.settings, log)

        # Expect: sleep,get (hot), sleep,get (new) = 2 pairs
        # Every "get" must be immediately preceded by "sleep"
        for i, entry in enumerate(call_order):
            if entry == "get":
                assert i > 0 and call_order[i - 1] == "sleep", (
                    f"HTTP request at index {i} was not preceded by rate-limit sleep. "
                    f"Full sequence: {call_order}"
                )


class TestAdaptiveRateLimit:
    def test_exhausted_rate_limit_sleeps_for_reset(self):
        """When remaining <= 1, sleep for the full reset duration."""
        log = MagicMock()
        resp = _make_response({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "30"})

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 1.0, log)

        mock_sleep.assert_called_once_with(30.0)
        log.warning.assert_called_once()

    def test_low_rate_limit_sleeps_proportionally(self):
        """When remaining < 5, sleep for reset/remaining."""
        log = MagicMock()
        resp = _make_response({"x-ratelimit-remaining": "3", "x-ratelimit-reset": "30"})

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 1.0, log)

        mock_sleep.assert_called_once_with(10.0)  # 30 / 3
        log.info.assert_called_once()

    def test_healthy_rate_limit_sleeps_min_delay(self):
        """When remaining >= 5, sleep for min_delay."""
        log = MagicMock()
        resp = _make_response({"x-ratelimit-remaining": "50", "x-ratelimit-reset": "60"})

        with patch("aggre.collectors.reddit.collector.time.sleep") as mock_sleep:
            _rate_limit_sleep(resp, 2.0, log)

        mock_sleep.assert_called_once_with(2.0)

    def test_missing_headers_sleeps_min_delay(self):
        """When rate-limit headers are absent, fall back to min_delay."""
        log = MagicMock()
        resp = _make_response({})

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
        resp_429.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=resp_429
        )

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
    def test_creates_source_row(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        listing = _make_listing()

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings({"hot.json": listing, "new.json": listing})
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config.reddit, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "reddit"
            assert rows[0].name == "python"
            src_config = json.loads(rows[0].config)
            assert src_config["subreddit"] == "python"
            assert rows[0].last_fetched_at is not None

    def test_reuses_existing_source(self, engine):
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        listing = _make_listing()

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_client_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings({"hot.json": listing, "new.json": listing})
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config.reddit, config.settings, log)
            collector.collect(engine, config.reddit, config.settings, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(Source)).fetchall()
            assert len(rows) == 1


class TestRedditCollectorProxy:
    def test_collect_passes_proxy_url_to_http_client(self, engine):
        """collect() should pass proxy_url from config to create_http_client."""
        config = AppConfig(
            reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
            settings=Settings(reddit_rate_limit=0.0, proxy_url="socks5://tor-proxy:9150"),
        )
        log = MagicMock()
        collector = RedditCollector()

        listing = _make_listing()

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_factory, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings({"hot.json": listing, "new.json": listing})
            mock_factory.return_value = client_instance

            collector.collect(engine, config.reddit, config.settings, log)

        mock_factory.assert_called_with(proxy_url="socks5://tor-proxy:9150")

    def test_collect_passes_none_when_proxy_empty(self, engine):
        """collect() should pass proxy_url=None when config has empty proxy_url."""
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        listing = _make_listing()

        with patch("aggre.collectors.reddit.collector.create_http_client") as mock_factory, patch("aggre.collectors.reddit.collector.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = _fake_get_for_listings({"hot.json": listing, "new.json": listing})
            mock_factory.return_value = client_instance

            collector.collect(engine, config.reddit, config.settings, log)

        mock_factory.assert_called_with(proxy_url=None)
