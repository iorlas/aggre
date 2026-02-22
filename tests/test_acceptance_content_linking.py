"""Acceptance tests: each collector creates correct SilverContent + content_id linkage."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import sqlalchemy as sa
import structlog

from aggre.collectors.hackernews.collector import HackernewsCollector
from aggre.collectors.hackernews.config import HackernewsConfig, HackernewsSource
from aggre.collectors.huggingface.collector import HuggingfaceCollector
from aggre.collectors.huggingface.config import HuggingfaceConfig, HuggingfaceSource
from aggre.collectors.lobsters.collector import LobstersCollector
from aggre.collectors.lobsters.config import LobstersConfig, LobstersSource
from aggre.collectors.reddit.collector import RedditCollector
from aggre.collectors.reddit.config import RedditConfig, RedditSource
from aggre.collectors.rss.collector import RssCollector
from aggre.collectors.rss.config import RssConfig, RssSource
from aggre.collectors.youtube.collector import YoutubeCollector
from aggre.collectors.youtube.config import YoutubeConfig, YoutubeSource
from aggre.config import AppConfig
from aggre.db import SilverContent, SilverDiscussion
from aggre.settings import Settings


def _log():
    return structlog.get_logger()


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------


def _rss_fake_entry(**kwargs):
    defaults = {
        "id": "rss-entry-1",
        "title": "RSS Post",
        "link": "https://example.com/article",
        "author": "Alice",
        "summary": "RSS summary",
        "published": "2025-01-01T00:00:00Z",
    }
    defaults.update(kwargs)
    data = {k: v for k, v in defaults.items() if v is not None}

    class Entry(dict):
        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError:
                return None

    return Entry(data)


def _rss_fake_feed(entries, feed_title="Test Feed"):
    feed_meta = {"title": feed_title}

    class FeedMeta:
        def get(self, key, default=None):
            return feed_meta.get(key, default)

    class Feed:
        def __init__(self, entries, feed):
            self.entries = entries
            self.feed = feed
            self.bozo = False

    return Feed(entries, FeedMeta())


class TestRssContentLinking:
    def test_creates_silver_content_with_correct_url_and_domain(self, engine):
        config = AppConfig(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))

        entry = _rss_fake_entry(
            id="post-1",
            link="https://example.com/article",
        )
        feed = _rss_fake_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            RssCollector().collect(engine, config.rss, config.settings, _log())

        with engine.connect() as conn:
            sc_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(sc_rows) == 1
            assert sc_rows[0].canonical_url == "https://example.com/article"
            assert sc_rows[0].domain == "example.com"

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 1
            assert sd_rows[0].content_id == sc_rows[0].id


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------


def _reddit_make_post(
    post_id="abc123",
    title="Reddit Post",
    subreddit="python",
    url="https://example.com/article",
    is_self=False,
):
    return {
        "kind": "t3",
        "data": {
            "name": f"t3_{post_id}",
            "title": title,
            "author": "redditor",
            "selftext": "",
            "permalink": f"/r/{subreddit}/comments/{post_id}/test/",
            "created_utc": 1700000000.0,
            "score": 42,
            "num_comments": 5,
            "link_flair_text": None,
            "subreddit": subreddit,
            "url": url,
            "is_self": is_self,
        },
    }


def _reddit_make_listing(*posts):
    return {"data": {"children": list(posts)}}


def _reddit_fake_get(mock_responses):
    def fake_get(url):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {}
        for key, data in mock_responses.items():
            if key in url:
                resp.json.return_value = data
                return resp
        resp.json.return_value = _reddit_make_listing()
        return resp

    return fake_get


class TestRedditContentLinking:
    def test_link_post_creates_silver_content(self, engine):
        config = AppConfig(
            reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
            settings=Settings(reddit_rate_limit=0.0),
        )

        post = _reddit_make_post(url="https://example.com/article", is_self=False)
        listing = _reddit_make_listing(post)
        mock_responses = {"hot.json": listing, "new.json": listing}

        with patch("aggre.collectors.reddit.collector.httpx.Client") as mock_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client = MagicMock()
            client.get.side_effect = _reddit_fake_get(mock_responses)
            mock_cls.return_value = client
            RedditCollector().collect(engine, config.reddit, config.settings, MagicMock())

        with engine.connect() as conn:
            sc_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(sc_rows) == 1
            assert sc_rows[0].canonical_url == "https://example.com/article"
            assert sc_rows[0].domain == "example.com"

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 1
            assert sd_rows[0].content_id == sc_rows[0].id

    def test_self_post_no_silver_content(self, engine):
        """Self-posts (is_self=True) should NOT create a SilverContent row."""
        config = AppConfig(
            reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
            settings=Settings(reddit_rate_limit=0.0),
        )

        post = _reddit_make_post(is_self=True, url="https://reddit.com/r/python/comments/abc123/test/")
        listing = _reddit_make_listing(post)
        mock_responses = {"hot.json": listing, "new.json": listing}

        with patch("aggre.collectors.reddit.collector.httpx.Client") as mock_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client = MagicMock()
            client.get.side_effect = _reddit_fake_get(mock_responses)
            mock_cls.return_value = client
            RedditCollector().collect(engine, config.reddit, config.settings, MagicMock())

        with engine.connect() as conn:
            sc_count = conn.execute(sa.select(sa.func.count()).select_from(SilverContent)).scalar()
            assert sc_count == 0

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 1
            assert sd_rows[0].content_id is None

    def test_score_and_comment_count_populated(self, engine):
        config = AppConfig(
            reddit=RedditConfig(sources=[RedditSource(subreddit="python")]),
            settings=Settings(reddit_rate_limit=0.0),
        )

        post = _reddit_make_post(url="https://example.com/article", is_self=False)
        post["data"]["score"] = 99
        post["data"]["num_comments"] = 12
        listing = _reddit_make_listing(post)
        mock_responses = {"hot.json": listing, "new.json": listing}

        with patch("aggre.collectors.reddit.collector.httpx.Client") as mock_cls, patch("aggre.collectors.reddit.collector.time.sleep"):
            client = MagicMock()
            client.get.side_effect = _reddit_fake_get(mock_responses)
            mock_cls.return_value = client
            RedditCollector().collect(engine, config.reddit, config.settings, MagicMock())

        with engine.connect() as conn:
            sd = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert sd.score == 99
            assert sd.comment_count == 12


# ---------------------------------------------------------------------------
# HackerNews
# ---------------------------------------------------------------------------


def _hn_make_hit(object_id="12345", title="HN Story", url="https://example.com/article"):
    return {
        "objectID": object_id,
        "title": title,
        "author": "pg",
        "url": url,
        "points": 100,
        "num_comments": 25,
        "created_at": "2024-01-15T12:00:00.000Z",
    }


def _hn_search_response(*hits):
    return {"hits": list(hits)}


def _hn_mock_client(responses):
    client = MagicMock()

    def fake_get(url):
        resp = MagicMock()
        resp.status_code = 200
        for pattern, data in responses.items():
            if pattern in url:
                resp.json.return_value = data
                return resp
        resp.json.return_value = {"hits": []}
        return resp

    client.get.side_effect = fake_get
    return client


class TestHackernewsContentLinking:
    def test_creates_silver_content_for_external_url(self, engine):
        config = AppConfig(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]),
            settings=Settings(hn_rate_limit=0.0),
        )

        hit = _hn_make_hit(url="https://example.com/article")
        responses = {"search_by_date": _hn_search_response(hit)}

        with (
            patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            mock_cls.return_value = _hn_mock_client(responses)
            HackernewsCollector().collect(engine, config.hackernews, config.settings, MagicMock())

        with engine.connect() as conn:
            sc_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(sc_rows) == 1
            assert sc_rows[0].canonical_url == "https://example.com/article"
            assert sc_rows[0].domain == "example.com"

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 1
            assert sd_rows[0].content_id == sc_rows[0].id

    def test_no_silver_content_for_ask_hn(self, engine):
        """Ask HN stories with no external URL should NOT create SilverContent."""
        config = AppConfig(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]),
            settings=Settings(hn_rate_limit=0.0),
        )

        hit = _hn_make_hit(object_id="999")
        hit["url"] = None  # Ask HN / Show HN with no external URL
        responses = {"search_by_date": _hn_search_response(hit)}

        with (
            patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            mock_cls.return_value = _hn_mock_client(responses)
            HackernewsCollector().collect(engine, config.hackernews, config.settings, MagicMock())

        with engine.connect() as conn:
            sc_count = conn.execute(sa.select(sa.func.count()).select_from(SilverContent)).scalar()
            assert sc_count == 0

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 1
            assert sd_rows[0].content_id is None

    def test_score_and_comment_count_populated(self, engine):
        config = AppConfig(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]),
            settings=Settings(hn_rate_limit=0.0),
        )

        hit = _hn_make_hit()
        hit["points"] = 200
        hit["num_comments"] = 50
        responses = {"search_by_date": _hn_search_response(hit)}

        with (
            patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            mock_cls.return_value = _hn_mock_client(responses)
            HackernewsCollector().collect(engine, config.hackernews, config.settings, MagicMock())

        with engine.connect() as conn:
            sd = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert sd.score == 200
            assert sd.comment_count == 50


# ---------------------------------------------------------------------------
# Lobsters
# ---------------------------------------------------------------------------


def _lob_make_story(short_id="abc123", url="https://example.com/article", score=10, comment_count=3):
    return {
        "short_id": short_id,
        "title": "Lobsters Story",
        "url": url,
        "score": score,
        "comment_count": comment_count,
        "tags": ["programming"],
        "submitter_user": "testuser",
        "created_at": "2024-01-15T12:00:00.000Z",
        "comments_url": f"https://lobste.rs/s/{short_id}",
    }


def _lob_mock_client(responses):
    client = MagicMock()

    def fake_get(url):
        resp = MagicMock()
        resp.status_code = 200
        for pattern, data in responses.items():
            if pattern in url:
                resp.json.return_value = data
                return resp
        resp.json.return_value = []
        return resp

    client.get.side_effect = fake_get
    return client


class TestLobstersContentLinking:
    def test_creates_silver_content_for_external_url(self, engine):
        config = AppConfig(
            lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]),
            settings=Settings(lobsters_rate_limit=0.0),
        )

        story = _lob_make_story(url="https://example.com/article")
        responses = {"hottest.json": [story], "newest.json": [story]}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _lob_mock_client(responses)
            LobstersCollector().collect(engine, config.lobsters, config.settings, MagicMock())

        with engine.connect() as conn:
            sc_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(sc_rows) == 1
            assert sc_rows[0].canonical_url == "https://example.com/article"
            assert sc_rows[0].domain == "example.com"

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 1
            assert sd_rows[0].content_id == sc_rows[0].id

    def test_score_and_comment_count_populated(self, engine):
        config = AppConfig(
            lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]),
            settings=Settings(lobsters_rate_limit=0.0),
        )

        story = _lob_make_story(score=77, comment_count=14)
        responses = {"hottest.json": [story], "newest.json": []}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _lob_mock_client(responses)
            LobstersCollector().collect(engine, config.lobsters, config.settings, MagicMock())

        with engine.connect() as conn:
            sd = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert sd.score == 77
            assert sd.comment_count == 14


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


def _yt_make_entry(video_id="vid001", title="YT Video"):
    return {
        "id": video_id,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "upload_date": "20240115",
        "duration": 600,
        "view_count": 1000,
    }


class TestYoutubeContentLinking:
    def test_creates_silver_content(self, engine):
        config = AppConfig(
            youtube=YoutubeConfig(sources=[YoutubeSource(channel_id="UC_test", name="Test Channel")], fetch_limit=10),
            settings=Settings(),
        )

        mock_ydl = MagicMock()
        mock_ydl.extract_info = lambda url, download=False: {"entries": [_yt_make_entry()]}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            YoutubeCollector().collect(engine, config.youtube, config.settings, _log())

        with engine.connect() as conn:
            sc_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(sc_rows) == 1
            # YouTube URL normalization: youtube.com/watch?v=vid001
            assert "youtube.com" in sc_rows[0].canonical_url
            assert "vid001" in sc_rows[0].canonical_url
            assert sc_rows[0].domain == "youtube.com"

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 1
            assert sd_rows[0].content_id == sc_rows[0].id


# ---------------------------------------------------------------------------
# HuggingFace
# ---------------------------------------------------------------------------


def _hf_make_paper(paper_id="2401.12345", title="HF Paper", upvotes=42, num_comments=5):
    return {
        "paper": {
            "id": paper_id,
            "title": title,
            "summary": "A summary.",
            "authors": [{"name": "Alice"}],
            "publishedAt": "2024-01-15T00:00:00.000Z",
            "upvotes": upvotes,
            "numComments": num_comments,
            "githubRepo": None,
        },
        "numComments": num_comments,
    }


def _hf_mock_client(papers):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = papers
    client.get.return_value = resp
    return client


class TestHuggingfaceContentLinking:
    def test_creates_silver_content(self, engine):
        config = AppConfig(
            huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HF Papers")]),
            settings=Settings(),
        )

        with patch("aggre.collectors.huggingface.collector.create_http_client") as mock_cls:
            mock_cls.return_value = _hf_mock_client([_hf_make_paper()])
            HuggingfaceCollector().collect(engine, config.huggingface, config.settings, MagicMock())

        with engine.connect() as conn:
            sc_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(sc_rows) == 1
            assert sc_rows[0].canonical_url == "https://huggingface.co/papers/2401.12345"
            assert sc_rows[0].domain == "huggingface.co"

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 1
            assert sd_rows[0].content_id == sc_rows[0].id

    def test_score_and_comment_count_populated(self, engine):
        config = AppConfig(
            huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HF Papers")]),
            settings=Settings(),
        )

        with patch("aggre.collectors.huggingface.collector.create_http_client") as mock_cls:
            mock_cls.return_value = _hf_mock_client([_hf_make_paper(upvotes=99, num_comments=7)])
            HuggingfaceCollector().collect(engine, config.huggingface, config.settings, MagicMock())

        with engine.connect() as conn:
            sd = conn.execute(sa.select(SilverDiscussion)).fetchone()
            assert sd.score == 99
            assert sd.comment_count == 7


# ---------------------------------------------------------------------------
# Cross-source deduplication
# ---------------------------------------------------------------------------


class TestCrossSourceDedup:
    def test_rss_and_hackernews_share_silver_content(self, engine):
        """Two collectors pointing at the same external URL should share one SilverContent row."""
        shared_url = "https://example.com/article"

        # 1. Run RSS collector
        rss_config = AppConfig(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))
        entry = _rss_fake_entry(id="rss-1", link=shared_url)
        feed = _rss_fake_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            RssCollector().collect(engine, rss_config.rss, rss_config.settings, _log())

        # 2. Run HackerNews collector
        hn_config = AppConfig(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]),
            settings=Settings(hn_rate_limit=0.0),
        )
        hit = _hn_make_hit(object_id="hn-42", url=shared_url)
        responses = {"search_by_date": _hn_search_response(hit)}

        with (
            patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            mock_cls.return_value = _hn_mock_client(responses)
            HackernewsCollector().collect(engine, hn_config.hackernews, hn_config.settings, MagicMock())

        # 3. Verify: exactly 1 SilverContent, 2 SilverDiscussions
        with engine.connect() as conn:
            sc_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(sc_rows) == 1, f"Expected 1 SilverContent, got {len(sc_rows)}"
            content_id = sc_rows[0].id

            sd_rows = conn.execute(sa.select(SilverDiscussion).order_by(SilverDiscussion.source_type)).fetchall()
            assert len(sd_rows) == 2
            source_types = {r.source_type for r in sd_rows}
            assert source_types == {"rss", "hackernews"}
            assert all(r.content_id == content_id for r in sd_rows)

    def test_lobsters_and_hackernews_share_silver_content(self, engine):
        """Lobsters and HN pointing at the same URL should share one SilverContent."""
        shared_url = "https://example.com/article"

        # 1. Run Lobsters collector
        lob_config = AppConfig(
            lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]),
            settings=Settings(lobsters_rate_limit=0.0),
        )
        story = _lob_make_story(short_id="lob-1", url=shared_url)
        responses = {"hottest.json": [story], "newest.json": []}

        with (
            patch("aggre.collectors.lobsters.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.lobsters.collector.time.sleep"),
        ):
            mock_cls.return_value = _lob_mock_client(responses)
            LobstersCollector().collect(engine, lob_config.lobsters, lob_config.settings, MagicMock())

        # 2. Run HN collector
        hn_config = AppConfig(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]),
            settings=Settings(hn_rate_limit=0.0),
        )
        hit = _hn_make_hit(object_id="hn-55", url=shared_url)
        responses = {"search_by_date": _hn_search_response(hit)}

        with (
            patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            mock_cls.return_value = _hn_mock_client(responses)
            HackernewsCollector().collect(engine, hn_config.hackernews, hn_config.settings, MagicMock())

        # 3. Verify
        with engine.connect() as conn:
            sc_rows = conn.execute(sa.select(SilverContent)).fetchall()
            assert len(sc_rows) == 1
            content_id = sc_rows[0].id

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 2
            source_types = {r.source_type for r in sd_rows}
            assert source_types == {"lobsters", "hackernews"}
            assert all(r.content_id == content_id for r in sd_rows)

    def test_url_normalization_dedup(self, engine):
        """URLs that differ only by www. prefix / trailing slash should share SilverContent."""
        # RSS with "https://www.example.com/article/"
        rss_config = AppConfig(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))
        entry = _rss_fake_entry(id="rss-norm", link="https://www.example.com/article/")
        feed = _rss_fake_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            RssCollector().collect(engine, rss_config.rss, rss_config.settings, _log())

        # HN with "https://example.com/article" (no www, no trailing slash)
        hn_config = AppConfig(
            hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]),
            settings=Settings(hn_rate_limit=0.0),
        )
        hit = _hn_make_hit(object_id="hn-norm", url="https://example.com/article")
        responses = {"search_by_date": _hn_search_response(hit)}

        with (
            patch("aggre.collectors.hackernews.collector.create_http_client") as mock_cls,
            patch("aggre.collectors.hackernews.collector.time.sleep"),
        ):
            mock_cls.return_value = _hn_mock_client(responses)
            HackernewsCollector().collect(engine, hn_config.hackernews, hn_config.settings, MagicMock())

        with engine.connect() as conn:
            sc_count = conn.execute(sa.select(sa.func.count()).select_from(SilverContent)).scalar()
            assert sc_count == 1, f"Expected 1 SilverContent after normalization, got {sc_count}"

            sd_rows = conn.execute(sa.select(SilverDiscussion)).fetchall()
            assert len(sd_rows) == 2
            assert all(r.content_id is not None for r in sd_rows)
            assert sd_rows[0].content_id == sd_rows[1].content_id
