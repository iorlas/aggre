"""Acceptance tests: each collector creates correct SilverContent + content_id linkage."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
from tests.factories import (
    hf_paper,
    hn_hit,
    hn_search_response,
    lobsters_story,
    make_config,
    reddit_listing,
    reddit_post,
    rss_entry,
    rss_feed,
    youtube_entry,
)
from tests.helpers import collect, get_contents, get_observations

pytestmark = pytest.mark.acceptance


class TestRssContentLinking:
    def test_creates_silver_content_with_correct_url_and_domain(self, engine):
        config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))

        entry = rss_entry(
            id="post-1",
            link="https://example.com/article",
        )
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collect(RssCollector(), engine, config.rss, config.settings)

        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1
        assert sc_rows[0].canonical_url == "https://example.com/article"
        assert sc_rows[0].domain == "example.com"

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 1
        assert sd_rows[0].content_id == sc_rows[0].id


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------


class TestRedditContentLinking:
    def test_link_post_creates_silver_content(self, engine, mock_http):
        config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))

        post = reddit_post(url="https://example.com/article", is_self=False)
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            collect(RedditCollector(), engine, config.reddit, config.settings)

        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1
        assert sc_rows[0].canonical_url == "https://example.com/article"
        assert sc_rows[0].domain == "example.com"

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 1
        assert sd_rows[0].content_id == sc_rows[0].id

    def test_self_post_creates_content_with_text(self, engine, mock_http):
        """Self-posts (is_self=True) create SilverContent with text already populated."""
        config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))

        post = reddit_post(is_self=True, url="https://reddit.com/r/python/comments/abc123/test/")
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            collect(RedditCollector(), engine, config.reddit, config.settings)

        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1
        assert sc_rows[0].text is not None  # selftext populated

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 1
        assert sd_rows[0].content_id == sc_rows[0].id

    def test_score_and_comment_count_populated(self, engine, mock_http):
        config = make_config(reddit=RedditConfig(sources=[RedditSource(subreddit="python")]))

        post = reddit_post(url="https://example.com/article", is_self=False, score=99, num_comments=12)
        listing = reddit_listing(post)
        mock_http.get(url__regex=r".*/hot\.json.*").respond(json=listing)
        mock_http.get(url__regex=r".*/new\.json.*").respond(json=listing)

        with patch("aggre.collectors.reddit.collector.time.sleep"):
            collect(RedditCollector(), engine, config.reddit, config.settings)

        sd = get_observations(engine)[0]
        assert sd.score == 99
        assert sd.comment_count == 12


# ---------------------------------------------------------------------------
# HackerNews
# ---------------------------------------------------------------------------


class TestHackernewsContentLinking:
    def test_creates_silver_content_for_external_url(self, engine, mock_http):
        config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]))

        hit = hn_hit(url="https://example.com/article")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(json=hn_search_response(hit))

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1
        assert sc_rows[0].canonical_url == "https://example.com/article"
        assert sc_rows[0].domain == "example.com"

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 1
        assert sd_rows[0].content_id == sc_rows[0].id

    def test_no_silver_content_for_ask_hn(self, engine, mock_http):
        """Ask HN stories with no external URL should NOT create SilverContent."""
        config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]))

        hit = hn_hit(object_id="999")
        hit["url"] = None  # Ask HN / Show HN with no external URL
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(json=hn_search_response(hit))

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        assert len(get_contents(engine)) == 0

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 1
        assert sd_rows[0].content_id is None

    def test_score_and_comment_count_populated(self, engine, mock_http):
        config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]))

        hit = hn_hit(points=200, num_comments=50)
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(json=hn_search_response(hit))

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(HackernewsCollector(), engine, config.hackernews, config.settings)

        sd = get_observations(engine)[0]
        assert sd.score == 200
        assert sd.comment_count == 50


# ---------------------------------------------------------------------------
# Lobsters
# ---------------------------------------------------------------------------


class TestLobstersContentLinking:
    def test_creates_silver_content_for_external_url(self, engine, mock_http):
        config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))

        story = lobsters_story(url="https://example.com/article")
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[story])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1
        assert sc_rows[0].canonical_url == "https://example.com/article"
        assert sc_rows[0].domain == "example.com"

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 1
        assert sd_rows[0].content_id == sc_rows[0].id

    def test_score_and_comment_count_populated(self, engine, mock_http):
        config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))

        story = lobsters_story(score=77, comment_count=14)
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            collect(LobstersCollector(), engine, config.lobsters, config.settings)

        sd = get_observations(engine)[0]
        assert sd.score == 77
        assert sd.comment_count == 14


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------


class TestYoutubeContentLinking:
    def test_creates_silver_content(self, engine):
        config = make_config(youtube=YoutubeConfig(sources=[YoutubeSource(channel_id="UC_test", name="Test Channel")], fetch_limit=10))

        mock_ydl = MagicMock()
        mock_ydl.extract_info = lambda url, download=False: {"entries": [youtube_entry()]}
        mock_ydl.__enter__ = lambda s: s
        mock_ydl.__exit__ = MagicMock(return_value=False)

        with patch("aggre.collectors.youtube.collector.yt_dlp.YoutubeDL", return_value=mock_ydl):
            collect(YoutubeCollector(), engine, config.youtube, config.settings)

        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1
        # YouTube URL normalization: youtube.com/watch?v=vid001
        assert "youtube.com" in sc_rows[0].canonical_url
        assert "vid001" in sc_rows[0].canonical_url
        assert sc_rows[0].domain == "youtube.com"

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 1
        assert sd_rows[0].content_id == sc_rows[0].id


# ---------------------------------------------------------------------------
# HuggingFace
# ---------------------------------------------------------------------------


class TestHuggingfaceContentLinking:
    def test_creates_silver_content(self, engine, mock_http):
        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HF Papers")]))

        mock_http.get("https://huggingface.co/api/daily_papers").respond(json=[hf_paper()])

        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1
        assert sc_rows[0].canonical_url == "https://huggingface.co/papers/2401.12345"
        assert sc_rows[0].domain == "huggingface.co"

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 1
        assert sd_rows[0].content_id == sc_rows[0].id

    def test_score_and_comment_count_populated(self, engine, mock_http):
        config = make_config(huggingface=HuggingfaceConfig(sources=[HuggingfaceSource(name="HF Papers")]))

        mock_http.get("https://huggingface.co/api/daily_papers").respond(json=[hf_paper(upvotes=99, num_comments=7)])

        collect(HuggingfaceCollector(), engine, config.huggingface, config.settings)

        sd = get_observations(engine)[0]
        assert sd.score == 99
        assert sd.comment_count == 7


# ---------------------------------------------------------------------------
# Cross-source deduplication
# ---------------------------------------------------------------------------


class TestCrossSourceDedup:
    def test_rss_and_hackernews_share_silver_content(self, engine, mock_http):
        """Two collectors pointing at the same external URL should share one SilverContent row."""
        shared_url = "https://example.com/article"

        # 1. Run RSS collector
        rss_config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))
        entry = rss_entry(id="rss-1", link=shared_url)
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collect(RssCollector(), engine, rss_config.rss, rss_config.settings)

        # 2. Run HackerNews collector
        hn_config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]))
        hit = hn_hit(object_id="hn-42", url=shared_url)
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(json=hn_search_response(hit))

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(HackernewsCollector(), engine, hn_config.hackernews, hn_config.settings)

        # 3. Verify: exactly 1 SilverContent, 2 SilverObservations
        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1, f"Expected 1 SilverContent, got {len(sc_rows)}"
        content_id = sc_rows[0].id

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 2
        source_types = {r.source_type for r in sd_rows}
        assert source_types == {"rss", "hackernews"}
        assert all(r.content_id == content_id for r in sd_rows)

    def test_lobsters_and_hackernews_share_silver_content(self, engine, mock_http):
        """Lobsters and HN pointing at the same URL should share one SilverContent."""
        shared_url = "https://example.com/article"

        # 1. Run Lobsters collector
        lob_config = make_config(lobsters=LobstersConfig(sources=[LobstersSource(name="Lobsters")]))
        story = lobsters_story(short_id="lob-1", url=shared_url)
        mock_http.get(url__regex=r"hottest\.json").respond(json=[story])
        mock_http.get(url__regex=r"newest\.json").respond(json=[])

        with patch("aggre.collectors.lobsters.collector.time.sleep"):
            collect(LobstersCollector(), engine, lob_config.lobsters, lob_config.settings)

        # 2. Run HN collector — reset mock_http for new routes
        mock_http.reset()
        hn_config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]))
        hit = hn_hit(object_id="hn-55", url=shared_url)
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(json=hn_search_response(hit))

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(HackernewsCollector(), engine, hn_config.hackernews, hn_config.settings)

        # 3. Verify
        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1
        content_id = sc_rows[0].id

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 2
        source_types = {r.source_type for r in sd_rows}
        assert source_types == {"lobsters", "hackernews"}
        assert all(r.content_id == content_id for r in sd_rows)

    def test_url_normalization_dedup(self, engine, mock_http):
        """URLs that differ only by www. prefix / trailing slash should share SilverContent."""
        # RSS with "https://www.example.com/article/"
        rss_config = make_config(rss=RssConfig(sources=[RssSource(name="Blog", url="https://example.com/feed")]))
        entry = rss_entry(id="rss-norm", link="https://www.example.com/article/")
        feed = rss_feed([entry])

        with patch("aggre.collectors.rss.collector.feedparser.parse", return_value=feed):
            collect(RssCollector(), engine, rss_config.rss, rss_config.settings)

        # HN with "https://example.com/article" (no www, no trailing slash)
        hn_config = make_config(hackernews=HackernewsConfig(sources=[HackernewsSource(name="HN")]))
        hit = hn_hit(object_id="hn-norm", url="https://example.com/article")
        mock_http.get(url__startswith="https://hn.algolia.com/api/v1/search_by_date").respond(json=hn_search_response(hit))

        with patch("aggre.collectors.hackernews.collector.time.sleep"):
            collect(HackernewsCollector(), engine, hn_config.hackernews, hn_config.settings)

        sc_rows = get_contents(engine)
        assert len(sc_rows) == 1, f"Expected 1 SilverContent after normalization, got {len(sc_rows)}"

        sd_rows = get_observations(engine)
        assert len(sd_rows) == 2
        assert all(r.content_id is not None for r in sd_rows)
        assert sd_rows[0].content_id == sd_rows[1].content_id
