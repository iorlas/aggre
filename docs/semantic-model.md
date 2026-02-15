# Semantic Data Model

## Entity Overview

```
Source             1-to-many    BronzeDiscussion (immutable raw API response)
Source             1-to-many    SilverDiscussion (one per discussion thread)
SilverContent      1-to-many    SilverDiscussion (many discussions can reference the same content)
BronzeDiscussion   1-to-1       SilverDiscussion (each raw discussion maps to one discussion)
```

## Entities

### Source
Configured data source (RSS feed, subreddit, YouTube channel, etc.).

| Column | Description |
|--------|-------------|
| type | Source type: rss, reddit, youtube, hackernews, lobsters, huggingface |
| name | Human-readable name from config.yaml |
| config | JSON blob of source-specific config (url, channel_id, subreddit) |
| enabled | Whether this source is active (default 1) |
| last_fetched_at | Timestamp of last successful collection |

### BronzeDiscussion
Immutable raw API response. Never updated after creation.

| Column | Description |
|--------|-------------|
| source_type | Matches Source.type for the origin |
| external_id | Source-specific unique identifier (post ID, paper ID, video ID) |
| raw_data | Full JSON response from the API |

**Dedup:** Unique on (source_type, external_id). ON CONFLICT DO NOTHING.

### SilverContent
One row per canonical URL. Represents the content artifact (article, video, paper) itself, independent of any discussion about it.

| Column | Description |
|--------|-------------|
| canonical_url | Normalized URL (see urls.py for normalization rules) |
| domain | Extracted domain for grouping/filtering |
| title | Page title (from trafilatura extraction) |
| body_text | Full text: article body (trafilatura), transcript (whisper), or paper abstract |
| fetch_status | pending -> fetched / skipped / failed |
| transcription_status | null (non-video) or pending -> downloading -> transcribing -> completed / failed |
| transcription_error | Error message if transcription failed |
| detected_language | ISO language code from whisper |
| enriched_at | Timestamp when HN/Lobsters enrichment was run for this URL |

**Ownership rules:**
- Transcription fields live here because transcription is a content-level concern (the video itself), not a discussion-level concern
- enriched_at lives here because enrichment searches for discussions about this content URL
- body_text serves dual purpose: article text from trafilatura OR transcript from whisper

### SilverDiscussion
One row per source discussion (HN thread, Reddit post, RSS entry, etc.). Multiple discussions can reference the same SilverContent.

| Column | Description |
|--------|-------------|
| source_type | Origin: rss, reddit, youtube, hackernews, lobsters, huggingface |
| external_id | Source-specific unique ID |
| content_id | FK to SilverContent (what this discussion is about) |
| source_id | FK to Source (which configured source) |
| bronze_discussion_id | FK to BronzeDiscussion (raw data) |
| title | Discussion title |
| author | Author name |
| url | URL of the discussion page |
| content_text | Discussion-specific text: Reddit selftext, RSS summary, HF abstract |
| published_at | When the discussion was published |
| comments_status | null (no comments) or pending -> done |
| comments_json | Raw comments JSON blob |
| score | Numeric score (upvotes, points) |
| comment_count | Number of comments |
| meta | Source-specific metadata only (see below) |

**Dedup:** Unique on (source_type, external_id). ON CONFLICT DO UPDATE for mutable fields (score, title, etc.).

**meta contents per source type:**
- hackernews: `{hn_url}`
- reddit: `{subreddit, flair}`
- lobsters: `{tags, lobsters_url}`
- youtube: `{channel_id, channel_name, duration, view_count}`
- huggingface: `{github_repo}`
- rss: `{feed_title}`

**What is NOT in meta** (stored as proper columns instead):
- score/points/upvotes (use `score` column)
- num_comments/comment_count (use `comment_count` column)
- comments_status (use `comments_status` column)
- enriched_at (lives on SilverContent)

## Process-Entity Mapping

| Process | Reads | Writes |
|---------|-------|--------|
| Collector (collect) | Source | BronzeDiscussion, SilverDiscussion, SilverContent (via ensure_content) |
| Comment fetcher | SilverDiscussion (comments_status=pending) | SilverDiscussion (comments_json, comments_status=done) |
| Content fetcher | SilverContent (fetch_status=pending) | SilverContent (body_text, fetch_status) |
| Transcriber | SilverContent (transcription_status in pending/downloading/transcribing) + SilverDiscussion (for video ID) | SilverContent (body_text, transcription_status) |
| Enrichment | SilverContent (enriched_at IS NULL) | SilverDiscussion (new HN/Lobsters discussions), SilverContent (enriched_at) |

## Status Lifecycles

### FetchStatus (SilverContent.fetch_status)
```
pending --> fetched    (article text extracted successfully)
        \-> skipped    (YouTube domain, PDF, etc.)
        \-> failed     (HTTP error, extraction error)
```

### TranscriptionStatus (SilverContent.transcription_status)
```
NULL ---------> (non-video content, no transcription needed)
pending ------> downloading --> transcribing --> completed
                    \               \
                     \-> failed      \-> failed
```

### CommentsStatus (SilverDiscussion.comments_status)
```
NULL ---------> (source doesn't support comments: RSS, HuggingFace)
pending ------> done
```
