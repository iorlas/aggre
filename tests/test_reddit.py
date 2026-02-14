"""Tests for the Reddit collector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import sqlalchemy as sa

from aggre.collectors.reddit import RedditCollector
from aggre.config import AppConfig, RedditSource, Settings
from aggre.db import content_items, metadata, raw_comments, raw_items, reddit_comments, sources


def _make_engine():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    return engine


def _make_config(subreddits: list[str] | None = None, rate_limit: float = 0.0) -> AppConfig:
    subs = subreddits or ["python"]
    return AppConfig(
        reddit=[RedditSource(subreddit=s) for s in subs],
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


class TestRedditCollectorPosts:
    def test_stores_posts_in_raw_and_content(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        post = _make_post()
        listing = _make_listing(post)
        comment_response = _make_comment_listing()

        mock_responses = {
            "hot.json": listing,
            "new.json": listing,
            "comments/abc123.json": comment_response,
        }

        def fake_get(url):
            resp = MagicMock()
            resp.status_code = 200
            for key, data in mock_responses.items():
                if key in url:
                    resp.json.return_value = data
                    return resp
            resp.json.return_value = _make_listing()
            return resp

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch("aggre.collectors.reddit.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            count = collector.collect(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(raw_items)).fetchall()
            assert len(raws) == 1
            assert raws[0].external_id == "t3_abc123"
            assert raws[0].source_type == "reddit"

            items = conn.execute(sa.select(content_items)).fetchall()
            assert len(items) == 1
            assert items[0].title == "Test Post"
            assert items[0].author == "testuser"
            assert items[0].source_type == "reddit"
            assert "reddit.com" in items[0].url
            assert items[0].content_text == "This is the body text"

            meta = json.loads(items[0].metadata)
            assert meta["subreddit"] == "python"
            assert meta["score"] == 42
            assert meta["num_comments"] == 5
            assert meta["flair"] == "Discussion"

    def test_dedup_same_post_in_hot_and_new(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        post = _make_post()
        listing = _make_listing(post)
        comment_response = _make_comment_listing()

        mock_responses = {
            "hot.json": listing,
            "new.json": listing,
            "comments/abc123.json": comment_response,
        }

        def fake_get(url):
            resp = MagicMock()
            resp.status_code = 200
            for key, data in mock_responses.items():
                if key in url:
                    resp.json.return_value = data
                    return resp
            resp.json.return_value = _make_listing()
            return resp

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch("aggre.collectors.reddit.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            count = collector.collect(engine, config, log)

        assert count == 1

        with engine.connect() as conn:
            raws = conn.execute(sa.select(raw_items)).fetchall()
            assert len(raws) == 1

            items = conn.execute(sa.select(content_items)).fetchall()
            assert len(items) == 1

    def test_multiple_unique_posts(self):
        engine = _make_engine()
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
            "comments/aaa.json": _make_comment_listing(),
            "comments/bbb.json": _make_comment_listing(),
        }

        def fake_get(url):
            resp = MagicMock()
            resp.status_code = 200
            for key, data in mock_responses.items():
                if key in url:
                    resp.json.return_value = data
                    return resp
            resp.json.return_value = _make_listing()
            return resp

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch("aggre.collectors.reddit.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            count = collector.collect(engine, config, log)

        assert count == 2

        with engine.connect() as conn:
            items = conn.execute(sa.select(content_items)).fetchall()
            assert len(items) == 2


class TestRedditCollectorComments:
    def test_stores_comments(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        post = _make_post()
        listing = _make_listing(post)
        comment = _make_comment(comment_id="com1", body="Great post!")
        comment_response = _make_comment_listing(comment)

        mock_responses = {
            "hot.json": listing,
            "new.json": listing,
            "comments/abc123.json": comment_response,
        }

        def fake_get(url):
            resp = MagicMock()
            resp.status_code = 200
            for key, data in mock_responses.items():
                if key in url:
                    resp.json.return_value = data
                    return resp
            resp.json.return_value = _make_listing()
            return resp

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch("aggre.collectors.reddit.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rcs = conn.execute(sa.select(raw_comments)).fetchall()
            assert len(rcs) == 1
            assert rcs[0].external_id == "t1_com1"

            reddits = conn.execute(sa.select(reddit_comments)).fetchall()
            assert len(reddits) == 1
            assert reddits[0].author == "commenter"
            assert reddits[0].body == "Great post!"
            assert reddits[0].score == 10
            assert reddits[0].parent_id == "t3_abc123"
            assert reddits[0].depth == 0

    def test_nested_comments(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        post = _make_post()
        listing = _make_listing(post)

        reply = _make_comment(comment_id="reply1", body="I agree", parent_id="t1_com1")
        parent_comment = _make_comment(
            comment_id="com1",
            body="Top level",
            replies={"data": {"children": [reply]}},
        )
        comment_response = _make_comment_listing(parent_comment)

        mock_responses = {
            "hot.json": listing,
            "new.json": listing,
            "comments/abc123.json": comment_response,
        }

        def fake_get(url):
            resp = MagicMock()
            resp.status_code = 200
            for key, data in mock_responses.items():
                if key in url:
                    resp.json.return_value = data
                    return resp
            resp.json.return_value = _make_listing()
            return resp

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch("aggre.collectors.reddit.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config, log)

        with engine.connect() as conn:
            reddits = conn.execute(sa.select(reddit_comments).order_by(reddit_comments.c.depth)).fetchall()
            assert len(reddits) == 2
            assert reddits[0].depth == 0
            assert reddits[0].body == "Top level"
            assert reddits[1].depth == 1
            assert reddits[1].body == "I agree"
            assert reddits[1].parent_id == "t1_com1"


class TestRedditCollectorRateLimit:
    def test_sleep_called_before_each_new_request(self):
        """Verify rate-limit sleep fires before each new HTTP request (listings + comments)."""
        engine = _make_engine()
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
            if "hot.json" in url:
                resp.json.return_value = hot_listing
            elif "comments/aaa" in url:
                resp.json.return_value = _make_comment_listing()
            else:
                resp.json.return_value = _make_listing()
            return resp

        def fake_sleep(seconds):
            if seconds == 2.0:
                call_order.append("sleep")

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch(
            "aggre.collectors.reddit.time.sleep", side_effect=fake_sleep
        ):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config, log)

        # Expect: sleep,get (hot), sleep,get (new), sleep,get (comments/aaa) = 3 pairs
        # Every "get" must be immediately preceded by "sleep"
        for i, entry in enumerate(call_order):
            if entry == "get":
                assert i > 0 and call_order[i - 1] == "sleep", (
                    f"HTTP request at index {i} was not preceded by rate-limit sleep. "
                    f"Full sequence: {call_order}"
                )

    def test_429_on_comments_does_not_skip_sleep_for_next_post(self):
        """After a 429 failure on post A's comments, post B's comments must still be rate-limited."""
        engine = _make_engine()
        config = _make_config(rate_limit=1.5)
        log = MagicMock()
        collector = RedditCollector()

        post1 = _make_post(post_id="aaa", title="First")
        post2 = _make_post(post_id="bbb", title="Second")
        hot_listing = _make_listing(post1, post2)

        # Track: for each unique request phase, did a rate-limit sleep precede it?
        call_order: list[str] = []

        def fake_get(url):
            if "comments/aaa" in url:
                call_order.append("get:aaa")
                from httpx import HTTPStatusError, Request, Response

                raise HTTPStatusError("429", request=Request("GET", url), response=Response(429))
            if "comments/bbb" in url:
                call_order.append("get:bbb")
            else:
                call_order.append("get:listing")
            resp = MagicMock()
            resp.status_code = 200
            if "hot.json" in url:
                resp.json.return_value = hot_listing
            elif "comments/bbb" in url:
                resp.json.return_value = _make_comment_listing()
            else:
                resp.json.return_value = _make_listing()
            return resp

        def fake_sleep(seconds):
            if seconds == 1.5:
                call_order.append("sleep")

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch(
            "aggre.collectors.reddit.time.sleep", side_effect=fake_sleep
        ):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config, log)

        # Find the first get:bbb and verify it was preceded by a sleep
        # (even though get:aaa failed with 429 before it)
        bbb_idx = next(i for i, c in enumerate(call_order) if c == "get:bbb")
        preceding = call_order[bbb_idx - 1]
        assert preceding == "sleep", (
            f"Comment fetch for bbb was not preceded by rate-limit sleep after aaa's 429 failure. "
            f"Preceding entry: {preceding}. Full sequence: {call_order}"
        )


class TestRedditCollectorSources:
    def test_creates_source_row(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        listing = _make_listing()

        def fake_get(url):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = listing
            return resp

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch("aggre.collectors.reddit.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(sources)).fetchall()
            assert len(rows) == 1
            assert rows[0].type == "reddit"
            assert rows[0].name == "python"
            src_config = json.loads(rows[0].config)
            assert src_config["subreddit"] == "python"
            assert rows[0].last_fetched_at is not None

    def test_reuses_existing_source(self):
        engine = _make_engine()
        config = _make_config()
        log = MagicMock()
        collector = RedditCollector()

        listing = _make_listing()

        def fake_get(url):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = listing
            return resp

        with patch("aggre.collectors.reddit.httpx.Client") as mock_client_cls, patch("aggre.collectors.reddit.time.sleep"):
            client_instance = MagicMock()
            client_instance.get.side_effect = fake_get
            mock_client_cls.return_value = client_instance

            collector.collect(engine, config, log)
            collector.collect(engine, config, log)

        with engine.connect() as conn:
            rows = conn.execute(sa.select(sources)).fetchall()
            assert len(rows) == 1
