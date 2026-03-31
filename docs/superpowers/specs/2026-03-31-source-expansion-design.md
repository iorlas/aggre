# Source Expansion — Design Spec

## Overview

Expand Aggre's source coverage by activating the dormant Telegram collector, adding Bluesky and Twitter/X as new collectors, and expanding RSS feed configuration with company blogs and newsletters. This is the immediate, concrete work to close the biggest gaps identified in Research 091 (Aggre Source Expansion).

Research reference: `~/Documents/Knowledge/Researches/091-aggre-source-expansion/`

## Scope

Four work items, ordered by effort:

1. **Activate Telegram** — uncomment config, add AI-focused channels
2. **Add RSS feeds** — company blogs, newsletters, deploy RSSHub for sources without native RSS
3. **Bluesky collector** — new collector using the AT Protocol API
4. **Twitter/X collector** — new collector using TwitterAPI.io (third-party provider)

Out of scope: backtracking/canary pipeline (separate spec), Mastodon (deprioritized), HuggingFace Models API, Semantic Scholar trending, OpenReview polling.

---

## 1. Activate Telegram Collector

### What

The Telegram collector already exists (`src/aggre/collectors/telegram/collector.py`), is registered in the registry, but is commented out in `config.yaml`. Activate it and add high-value AI channels.

### Config Changes

Uncomment the `telegram` section in `config.yaml` and replace placeholder channels:

```yaml
telegram:
  fetch_limit: 100
  init_fetch_limit: 1000
  sources:
    # Russian AI (unique signal, not available elsewhere)
    - username: "gonzo_ML"
      name: "Gonzo ML (Grisha Sapunov)"
      categories: ["ai", "ml", "papers"]
    - username: "doomgrad"
      name: "Doomgrad (technical AI articles)"
      categories: ["ai", "ml"]
    - username: "opendatascience"
      name: "Open Data Science"
      categories: ["ai", "ml", "data-science"]
    # English AI aggregation
    - username: "ai_newz"
      name: "AI Newz"
      categories: ["ai", "news"]
    - username: "DeepLearning_ai"
      name: "Deep Learning AI"
      categories: ["ai", "ml"]
```

### Prerequisites

- `AGGRE_TELEGRAM_API_ID`, `AGGRE_TELEGRAM_API_HASH`, `AGGRE_TELEGRAM_SESSION` env vars must be set
- Session string obtained via Telethon interactive auth (one-time manual step)

### Wiring

Already wired in registry. Needs:
- Uncomment config in `config.yaml`
- Add to collection workflow schedule in `src/aggre/workflows/collection.py` if not already present
- Verify the collector still works with current `BaseCollector` interface (it was written during Dagster era, may need minor adjustments for Hatchet)

### Testing

- Integration test with VCR cassette for one public channel
- Verify upsert semantics (view/forward counts update on re-fetch)

---

## 2. Expand RSS Feeds

### What

Add company engineering blogs and top AI newsletters to the existing RSS collector config. Deploy a self-hosted RSSHub instance for sources without native RSS.

### New RSS Sources

Add to `config.yaml` under `rss.sources`:

```yaml
# Company blogs (primary announcement sources)
- name: "OpenAI Blog"
  url: "https://openai.com/news/rss.xml"
  categories: ["ai", "openai", "announcements"]
- name: "Google AI Blog"
  url: "https://research.google/blog/rss"
  categories: ["ai", "google", "research"]
- name: "Microsoft Research"
  url: "https://www.microsoft.com/en-us/research/feed/"
  categories: ["ai", "microsoft", "research"]
- name: "NVIDIA Developer Blog"
  url: "https://developer.nvidia.com/blog/feed"
  categories: ["ai", "nvidia", "gpu"]

# Newsletters (high-signal analysis)
- name: "Import AI (Jack Clark)"
  url: "https://importai.substack.com/feed"
  categories: ["ai", "policy", "newsletter"]
- name: "Interconnects (Nathan Lambert)"
  url: "https://www.interconnects.ai/feed"
  categories: ["ai", "rlhf", "newsletter"]
- name: "Ahead of AI (Sebastian Raschka)"
  url: "https://magazine.sebastianraschka.com/feed"
  categories: ["ai", "ml", "newsletter"]
```

### RSSHub Deployment

For sources without native RSS (Anthropic blog, Meta AI blog):
- Deploy RSSHub via Docker on existing infrastructure
- Add RSSHub-generated feeds to config once running
- This is an infrastructure task, not a code change — document in deployment notes

### Testing

No code changes needed. Verify new feeds parse correctly by running collector once manually.

---

## 3. Bluesky Collector

### Data Model Mapping

No schema changes. Bluesky maps to existing tables.

#### SilverContent

One row per linked URL in a post (if the post links to external content):
- `canonical_url` = linked URL
- Created via `ensure_content(conn, url)`

Posts without external links: use `_ensure_self_post_content()` with the post text.

#### SilverDiscussion

| Field | Value |
|-------|-------|
| `source_type` | `"bluesky"` |
| `external_id` | AT URI: `at://did:plc:xxx/app.bsky.feed.post/yyy` |
| `url` | `https://bsky.app/profile/{handle}/post/{rkey}` |
| `title` | First 200 chars of post text (Bluesky has no titles) |
| `author` | `@{handle}` |
| `score` | `like_count + repost_count` |
| `comment_count` | `reply_count` |
| `content_id` | FK to SilverContent (linked URL or self-post) |
| `content_text` | Full post text |
| `published_at` | Post `created_at` timestamp |
| `meta` | `{"like_count": N, "repost_count": N, "reply_count": N, "labels": [...], "feed": "ml-feed"}` |

#### Source

One row per monitored feed or account list:
- `type = "bluesky"`, `name = "ML Feed"` (or account list name)

### Collector Implementation

#### Config (`src/aggre/collectors/bluesky/config.py`)

```python
class BlueskySource(SourceBase):
    name: str
    # One of these must be set:
    feed_uri: str = ""       # AT URI of a feed generator (e.g., ML Feed Blend)
    actor: str = ""          # DID or handle to fetch author feed
    search_query: str = ""   # Search term (e.g., "#machinelearning")

class BlueskyConfig(BaseModel):
    fetch_limit: int = 50
    init_fetch_limit: int = 200
    sources: list[BlueskySource] = []
```

#### Authentication

Bluesky public API (`public.api.bsky.app`) requires no auth for reading public feeds. For higher rate limits or private content, use app password auth:

Settings additions:
```python
bluesky_handle: str = ""        # e.g., "aggre.bsky.social"
bluesky_app_password: str = ""  # App password from settings
bluesky_rate_limit: float = 1.0
```

Auth is optional — start with unauthenticated public API access.

#### HTTP Client

Use `httpx` (no SDK dependency needed). The AT Protocol is a standard REST API:

- **Feed generator**: `GET /xrpc/app.bsky.feed.getFeed?feed={uri}&limit={n}`
- **Author feed**: `GET /xrpc/app.bsky.feed.getAuthorFeed?actor={did}&limit={n}`
- **Search**: `GET /xrpc/app.bsky.feed.searchPosts?q={query}&limit={n}`

Base URL: `https://public.api.bsky.app`

#### collect_discussions()

1. For each source in config:
   - Call appropriate endpoint (feed/author/search)
   - Parse JSON response, extract posts
   - Write bronze JSON per post
   - Return `DiscussionRef` list

2. Rate limiting: respect 3,000 req/5min. Use `settings.bluesky_rate_limit` between requests.

3. Pagination: use `cursor` parameter for subsequent pages up to `fetch_limit`.

#### process_discussion()

1. Extract first URL from post facets (rich text links) if present → `ensure_content()`
2. If no external URL, use `_ensure_self_post_content()` with post text
3. Upsert discussion with engagement metrics

#### Upsert Semantics

- Key: `(source_type, external_id)` where `external_id` is the AT URI
- Update columns on re-fetch: `score`, `comment_count`, `meta` (engagement counts change)

### Initial Feed Configuration

```yaml
bluesky:
  fetch_limit: 50
  init_fetch_limit: 200
  sources:
    - name: "ML Feed Blend"
      feed_uri: "at://did:plc:XXX/app.bsky.feed.generator/ml-feed"
      categories: ["ai", "ml"]
    - name: "Paper Skygest"
      feed_uri: "at://did:plc:XXX/app.bsky.feed.generator/paper-skygest"
      categories: ["ai", "papers"]
```

Feed URIs need to be discovered at implementation time (look up the feed generators on Bluesky).

### Schedule

Every 2 hours (`0 */2 * * *`). Bluesky feed content refreshes frequently but isn't as time-critical as Twitter.

### Testing

- VCR cassettes for public API responses (feed, author, search endpoints)
- Test facet/link extraction from post rich text
- Test upsert with changing engagement counts

---

## 4. Twitter/X Collector

### Access Method

Use **TwitterAPI.io** — a third-party provider at ~$0.15/1,000 tweets (~$5/mo for our volume). REST JSON API, no OAuth complexity.

Rationale: Official X API is $200+/mo for insufficient quota. TwitterAPI.io assumes scraping risk. At $5/mo, easy to swap providers if they go down.

### Data Model Mapping

No schema changes.

#### SilverContent

Same pattern as Bluesky — one row per linked URL in a tweet.

#### SilverDiscussion

| Field | Value |
|-------|-------|
| `source_type` | `"twitter"` |
| `external_id` | Tweet ID (string) |
| `url` | `https://x.com/{username}/status/{tweet_id}` |
| `title` | First 200 chars of tweet text |
| `author` | `@{username}` |
| `score` | `like_count + retweet_count` |
| `comment_count` | `reply_count` |
| `content_id` | FK to SilverContent (linked URL or self-post) |
| `content_text` | Full tweet text |
| `published_at` | Tweet `created_at` timestamp |
| `meta` | `{"like_count": N, "retweet_count": N, "reply_count": N, "quote_count": N, "bookmark_count": N, "view_count": N}` |

#### Source

One row per monitored Twitter list or account group:
- `type = "twitter"`, `name = "AI Researchers"` (or list name)

### Collector Implementation

#### Config (`src/aggre/collectors/twitter/config.py`)

```python
class TwitterSource(SourceBase):
    name: str
    # One of these:
    usernames: list[str] = []    # List of @handles to fetch timelines
    list_id: str = ""            # Twitter List ID
    search_query: str = ""       # Search query (e.g., "from:_akhaliq")

class TwitterConfig(BaseModel):
    fetch_limit: int = 50
    init_fetch_limit: int = 200
    sources: list[TwitterSource] = []
```

#### Authentication

Settings additions:
```python
twitter_api_key: str = ""          # TwitterAPI.io API key
twitter_api_base_url: str = "https://api.twitterapi.io"
twitter_rate_limit: float = 1.0
```

#### HTTP Client

Use `httpx`. TwitterAPI.io endpoints (based on their docs):

- **User timeline**: `GET /twitter/user/last_tweets?userName={handle}&count={n}`
- **Search**: `GET /twitter/tweet/advanced_search?query={q}&count={n}`
- **List timeline**: `GET /twitter/list/tweets?listId={id}&count={n}`

Auth: `x-api-key: {api_key}` header.

#### collect_discussions()

1. For each source:
   - If `usernames`: fetch timeline for each username
   - If `list_id`: fetch list timeline (more efficient for many accounts)
   - If `search_query`: search endpoint
2. Parse tweets, write bronze JSON
3. Return `DiscussionRef` list

Twitter Lists are the recommended approach for monitoring 100+ accounts efficiently — one API call instead of N timeline fetches.

#### process_discussion()

1. Extract URLs from tweet entities → `ensure_content()` for each
2. If no external URL, `_ensure_self_post_content()` with tweet text
3. Upsert discussion

#### Provider Abstraction

Since we may need to swap TwitterAPI.io for another provider, keep the HTTP layer thin:

```python
class TwitterAPIClient:
    """Thin wrapper over TwitterAPI.io. Replace this class to swap providers."""
    def __init__(self, api_key: str, base_url: str): ...
    def get_user_tweets(self, username: str, count: int) -> list[dict]: ...
    def search_tweets(self, query: str, count: int) -> list[dict]: ...
```

This is NOT a generic abstraction — it's specific to our use case, just isolated for swappability.

### Initial Configuration

```yaml
twitter:
  fetch_limit: 50
  init_fetch_limit: 200
  sources:
    - name: "AI Paper Scouts"
      usernames: ["_akhaliq", "aaborev"]
      categories: ["ai", "papers"]
    - name: "AI Researchers"
      usernames: ["kaborath", "ylecun", "fchollet", "jimfan", "ClaborevD"]
      categories: ["ai", "research"]
    - name: "AI Engineers"
      usernames: ["simonw", "natolambert", "Teknium1", "BlancheMinerva", "swyx"]
      categories: ["ai", "engineering"]
    - name: "AI Infrastructure"
      usernames: ["dylan522p"]
      categories: ["ai", "infrastructure"]
```

Note: actual Twitter handles need verification at implementation time — some may have changed.

### Schedule

Every 1 hour (`0 * * * *`). Twitter is the fastest-moving platform — hourly ensures we catch posts within a reasonable window.

### Cost Management

At ~$0.15/1K tweets and ~20 accounts fetching 50 tweets each = 1,000 tweets/run, 24 runs/day = 24,000 tweets/day = ~$3.60/day = ~$108/month at full scale.

Start smaller: fetch_limit=20, fewer accounts → ~$1/day. Scale up as needed.

### Testing

- VCR cassettes for TwitterAPI.io responses
- Test URL extraction from tweet entities
- Test provider client isolation (mock the client, test collector logic)

---

## Wiring Checklist (all collectors)

For each new collector (Bluesky, Twitter):

1. `src/aggre/collectors/{name}/__init__.py`
2. `src/aggre/collectors/{name}/config.py`
3. `src/aggre/collectors/{name}/collector.py`
4. `src/aggre/collectors/registry.py` — add entry
5. `src/aggre/config.py` — add config field to `AppConfig`
6. `src/aggre/settings.py` — add credentials/rate limit settings
7. `src/aggre/workflows/collection.py` — add to `_SOURCES` with cron schedule
8. `config.yaml` — add source configuration
9. `docs/guidelines/semantic-model.md` — document `source_type`, `meta` semantics, `score` semantics

For Telegram activation:
- Steps 6-9 only (collector already exists)

For RSS expansion:
- Step 8 only (add feed URLs to existing `rss.sources`)

---

## Implementation Order

1. RSS feed expansion (config-only, zero risk)
2. Telegram activation (config + env vars, existing code)
3. Bluesky collector (new code, free API, no cost risk)
4. Twitter/X collector (new code, paid API, cost risk)

Each is independently deployable and testable.
