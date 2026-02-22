# Aggre — Data Model & Query Reference

> Hybrid reference: YAML schema for exact column types + SQL query recipes for analysis.
> Use this document as context when writing SQL against the Aggre database.

## Schema

```yaml
tables:
  sources:
    description: Configured data sources (RSS feeds, subreddits, YouTube channels, etc.)
    columns:
      id:               { type: serial, pk: true }
      type:             { type: text, not_null: true, values: [rss, reddit, youtube, hackernews, lobsters, huggingface, telegram] }
      name:             { type: text, not_null: true, description: "Human-readable name from config.yaml" }
      config:           { type: text, not_null: true, description: "JSON blob — source-specific config (url, channel_id, subreddit)" }
      enabled:          { type: integer, default: 1, description: "1 = active, 0 = disabled" }
      created_at:       { type: text, default: "now()", description: "ISO 8601 timestamp" }
      last_fetched_at:  { type: text, nullable: true, description: "ISO 8601 — last successful collection" }

  silver_content:
    description: >
      One row per canonical URL. The content artifact (article, video, paper) itself,
      independent of discussions about it. This is the cross-source pivot table.
    columns:
      id:                     { type: serial, pk: true }
      canonical_url:          { type: text, not_null: true, unique: true, description: "Normalized URL (see urls.py)" }
      domain:                 { type: text, nullable: true, description: "Extracted domain for grouping/filtering" }
      title:                  { type: text, nullable: true, description: "Page title (from trafilatura extraction)" }
      body_text:              { type: text, nullable: true, description: "Article body (trafilatura) OR video transcript (whisper) — check transcription_status to distinguish" }
      fetch_status:           { type: text, not_null: true, default: "pending", values: [pending, downloaded, fetched, skipped, failed] }
      fetch_error:            { type: text, nullable: true, description: "Error message if fetch_status = failed" }
      fetched_at:             { type: text, nullable: true, description: "ISO 8601 — when content was fetched" }
      created_at:             { type: text, default: "now()", description: "ISO 8601 timestamp" }
      transcription_status:   { type: text, nullable: true, values: [pending, downloading, transcribing, completed, failed], description: "NULL for non-video content" }
      transcription_error:    { type: text, nullable: true }
      detected_language:      { type: text, nullable: true, description: "ISO language code from whisper" }
      enriched_at:            { type: text, nullable: true, description: "ISO 8601 — when HN/Lobsters enrichment was run for this URL" }
    indexes:
      - idx_silver_content_domain: { columns: [domain], where: "domain IS NOT NULL" }
      - idx_silver_content_fetch_status: [fetch_status]
      - idx_silver_content_transcription: { columns: [transcription_status], where: "transcription_status IS NOT NULL" }
      - idx_silver_content_enriched_at: { columns: [enriched_at], where: "enriched_at IS NULL" }

  silver_discussions:
    description: >
      One row per source discussion (HN thread, Reddit post, RSS entry, etc.).
      Multiple discussions can reference the same silver_content row — this is the
      cross-source join. The main table for analysis queries.
    columns:
      id:                     { type: serial, pk: true }
      source_id:              { type: integer, nullable: true, fk: "sources.id" }
      content_id:             { type: integer, nullable: true, fk: "silver_content.id", description: "NULL for self-posts or discussions without external links" }
      source_type:            { type: text, not_null: true, values: [rss, reddit, youtube, hackernews, lobsters, huggingface, telegram] }
      external_id:            { type: text, not_null: true, description: "Source-specific unique ID" }
      title:                  { type: text, nullable: true }
      author:                 { type: text, nullable: true }
      url:                    { type: text, nullable: true, description: "URL of the discussion page itself (not the linked content)" }
      content_text:           { type: text, nullable: true, description: "Reddit selftext, RSS summary, HF abstract, Telegram message text" }
      published_at:           { type: text, nullable: true, description: "ISO 8601 — when the discussion was published" }
      fetched_at:             { type: text, default: "now()", description: "ISO 8601 — when we collected it" }
      meta:                   { type: text, nullable: true, description: "JSON string — source-specific metadata (see meta section below)" }
      comments_status:        { type: text, nullable: true, values: [pending, done], description: "NULL = source doesn't support comments (RSS, HuggingFace, Telegram)" }
      comments_json:          { type: text, nullable: true, description: "Raw comments JSON blob" }
      score:                  { type: integer, nullable: true, description: "Platform-specific score — see score semantics below" }
      comment_count:          { type: integer, nullable: true }
    constraints:
      - unique: [source_type, external_id]
    indexes:
      - idx_silver_discussions_source_type: [source_type]
      - idx_silver_discussions_published: [published_at]
      - idx_silver_discussions_source_id: [source_id]
      - idx_silver_discussions_external: [source_type, external_id]
      - idx_silver_discussions_comments_status: { columns: [comments_status], where: "comments_status IS NOT NULL" }
      - idx_silver_discussions_url: { columns: [url], where: "url IS NOT NULL" }
      - idx_silver_discussions_content_id: { columns: [content_id], where: "content_id IS NOT NULL" }
```

### Relationships

```
sources.id              <--  silver_discussions.source_id
silver_content.id       <--  silver_discussions.content_id   (the cross-source pivot)
```

### `silver_discussions.meta` — JSON keys per source_type

Cast with `meta::jsonb` before querying.

| source_type  | Key            | Type       | Description                                |
|-------------|----------------|------------|--------------------------------------------|
| hackernews  | `hn_url`       | string     | HN discussion URL                          |
| reddit      | `subreddit`    | string     | Subreddit name                             |
| reddit      | `flair`        | string?    | Link flair text (can be null)              |
| lobsters    | `tags`         | string[]   | List of tag strings                        |
| lobsters    | `lobsters_url` | string     | Comments URL on Lobsters                   |
| youtube     | `channel_id`   | string     | YouTube channel ID                         |
| youtube     | `channel_name` | string     | YouTube channel name                       |
| youtube     | `duration`     | int?       | Video duration (seconds, can be null)      |
| youtube     | `view_count`   | int?       | View count at time of collection           |
| huggingface | `github_repo`  | string?    | Associated GitHub repo (can be null)       |
| rss         | `feed_title`   | string     | RSS feed title                             |
| telegram    | `forwards`     | int?       | Forward count (only present if > 0)        |
| telegram    | `media_type`   | string?    | E.g. "MessageMediaPhoto" (only if present) |

### `score` semantics per source_type

| source_type  | `score` means                    | `comment_count` means      |
|-------------|----------------------------------|----------------------------|
| hackernews  | HN points                       | Number of comments         |
| reddit      | Net upvotes                      | Number of comments         |
| lobsters    | Lobsters score                   | Number of comments         |
| youtube     | NULL (not collected)             | NULL (not collected)       |
| huggingface | Paper upvotes                    | Number of HF comments      |
| rss         | NULL (not applicable)            | NULL (not applicable)      |
| telegram    | View count (`msg.views`)         | Always 0                   |

---

## Caveats for Writing Correct SQL

1. **All timestamps are ISO 8601 text** — use `::timestamptz` for date arithmetic:
   ```sql
   WHERE published_at::timestamptz > now() - interval '24 hours'
   ```

2. **`meta` is JSON stored as text** — always cast before accessing:
   ```sql
   meta::jsonb->>'subreddit'
   ```

3. **`body_text` can be article text OR video transcript** — check `transcription_status` on `silver_content` to distinguish. If `transcription_status = 'completed'`, `body_text` is a transcript.

4. **`content_id` can be NULL** — some discussions don't link to external content (Reddit self-posts, Telegram messages, some HN "Ask HN" posts). Exclude NULLs for cross-source analysis.

5. **The cross-source pivot** is: `silver_discussions.content_id → silver_content.id`. Multiple discussions with the same `content_id` are different platforms discussing the same URL.

6. **`score` means different things** per platform — see the score semantics table above. Do not compare scores across source_types directly.

7. **YouTube `score` is NULL** — YouTube view counts are in `meta::jsonb->>'view_count'`, not in the `score` column.

8. **Enrichment creates discussions** — the enrichment process searches HN and Lobsters for existing discussions about collected URLs, creating new `silver_discussions` rows. Check `silver_content.enriched_at IS NOT NULL` to find content that has been enriched.

---

## Query Recipes

### Cross-Source Analysis

**Content discussed on 2+ platforms:**
```sql
SELECT
  sc.id,
  sc.canonical_url,
  sc.domain,
  sc.title,
  COUNT(DISTINCT sd.source_type) AS platform_count,
  ARRAY_AGG(DISTINCT sd.source_type) AS platforms
FROM silver_content sc
JOIN silver_discussions sd ON sd.content_id = sc.id
GROUP BY sc.id
HAVING COUNT(DISTINCT sd.source_type) >= 2
ORDER BY platform_count DESC;
```

**Top content by combined engagement:**
```sql
SELECT
  sc.canonical_url,
  sc.title,
  COUNT(DISTINCT sd.source_type) AS platforms,
  SUM(COALESCE(sd.score, 0)) AS total_score,
  SUM(COALESCE(sd.comment_count, 0)) AS total_comments,
  ARRAY_AGG(DISTINCT sd.source_type) AS source_types
FROM silver_content sc
JOIN silver_discussions sd ON sd.content_id = sc.id
GROUP BY sc.id
ORDER BY total_score + total_comments DESC
LIMIT 50;
```

**Content spread timeline — when each platform picked it up:**
```sql
SELECT
  sc.canonical_url,
  sc.title,
  sd.source_type,
  MIN(sd.published_at::timestamptz) AS first_seen,
  MAX(sd.published_at::timestamptz) AS last_seen
FROM silver_content sc
JOIN silver_discussions sd ON sd.content_id = sc.id
WHERE sc.id IN (
  SELECT content_id FROM silver_discussions
  WHERE content_id IS NOT NULL
  GROUP BY content_id
  HAVING COUNT(DISTINCT source_type) >= 2
)
GROUP BY sc.id, sd.source_type
ORDER BY sc.canonical_url, first_seen;
```

**Source overlap matrix — which platforms cover the same URLs:**
```sql
SELECT
  a.source_type AS source_a,
  b.source_type AS source_b,
  COUNT(DISTINCT a.content_id) AS shared_content
FROM silver_discussions a
JOIN silver_discussions b
  ON a.content_id = b.content_id
  AND a.source_type < b.source_type
WHERE a.content_id IS NOT NULL
GROUP BY a.source_type, b.source_type
ORDER BY shared_content DESC;
```

### Daily Digest

**Today's new discussions by source:**
```sql
SELECT
  source_type,
  COUNT(*) AS count,
  SUM(COALESCE(score, 0)) AS total_score
FROM silver_discussions
WHERE published_at::timestamptz >= CURRENT_DATE
GROUP BY source_type
ORDER BY count DESC;
```

**Most active domains today:**
```sql
SELECT
  sc.domain,
  COUNT(DISTINCT sc.id) AS content_count,
  COUNT(sd.id) AS discussion_count,
  SUM(COALESCE(sd.score, 0)) AS total_score
FROM silver_content sc
JOIN silver_discussions sd ON sd.content_id = sc.id
WHERE sd.published_at::timestamptz >= CURRENT_DATE
GROUP BY sc.domain
ORDER BY discussion_count DESC
LIMIT 20;
```

**New content with highest combined score:**
```sql
SELECT
  sc.canonical_url,
  sc.title,
  sc.domain,
  ARRAY_AGG(DISTINCT sd.source_type) AS sources,
  SUM(COALESCE(sd.score, 0)) AS total_score,
  SUM(COALESCE(sd.comment_count, 0)) AS total_comments
FROM silver_content sc
JOIN silver_discussions sd ON sd.content_id = sc.id
WHERE sd.published_at::timestamptz >= CURRENT_DATE
GROUP BY sc.id
ORDER BY total_score DESC
LIMIT 20;
```

### Deep Analysis

**Parsing meta JSON — examples per source_type:**
```sql
-- Reddit: filter by subreddit
SELECT * FROM silver_discussions
WHERE source_type = 'reddit'
  AND meta::jsonb->>'subreddit' = 'programming';

-- Lobsters: filter by tag
SELECT * FROM silver_discussions
WHERE source_type = 'lobsters'
  AND meta::jsonb->'tags' ? 'rust';

-- YouTube: videos longer than 30 min
SELECT *, (meta::jsonb->>'duration')::int / 60 AS minutes
FROM silver_discussions
WHERE source_type = 'youtube'
  AND (meta::jsonb->>'duration')::int > 1800;

-- YouTube: sort by view count
SELECT title, url, (meta::jsonb->>'view_count')::int AS views
FROM silver_discussions
WHERE source_type = 'youtube'
ORDER BY views DESC NULLS LAST
LIMIT 20;

-- HuggingFace: papers with GitHub repos
SELECT title, url, meta::jsonb->>'github_repo' AS repo
FROM silver_discussions
WHERE source_type = 'huggingface'
  AND meta::jsonb->>'github_repo' IS NOT NULL;

-- Telegram: messages with media
SELECT title, url, meta::jsonb->>'media_type' AS media
FROM silver_discussions
WHERE source_type = 'telegram'
  AND meta::jsonb->>'media_type' IS NOT NULL;
```

**Author activity across platforms:**
```sql
SELECT
  author,
  ARRAY_AGG(DISTINCT source_type) AS platforms,
  COUNT(*) AS total_posts,
  COUNT(DISTINCT source_type) AS platform_count
FROM silver_discussions
WHERE author IS NOT NULL
GROUP BY author
HAVING COUNT(DISTINCT source_type) >= 2
ORDER BY total_posts DESC
LIMIT 20;
```

**Content without body text (fetch gap analysis):**
```sql
SELECT
  fetch_status,
  COUNT(*) AS count
FROM silver_content
GROUP BY fetch_status;

-- Content that failed to fetch
SELECT canonical_url, domain, fetch_error
FROM silver_content
WHERE fetch_status = 'failed'
ORDER BY created_at::timestamptz DESC
LIMIT 20;

-- Content still pending fetch
SELECT canonical_url, domain, created_at
FROM silver_content
WHERE fetch_status = 'pending'
ORDER BY created_at::timestamptz DESC;
```

### Operational

**Pipeline health — status distributions:**
```sql
-- Content fetch status
SELECT fetch_status, COUNT(*) FROM silver_content GROUP BY fetch_status;

-- Transcription status
SELECT transcription_status, COUNT(*) FROM silver_content
WHERE transcription_status IS NOT NULL GROUP BY transcription_status;

-- Comments status
SELECT comments_status, COUNT(*) FROM silver_discussions
WHERE comments_status IS NOT NULL GROUP BY comments_status;

-- Discussions per source
SELECT source_type, COUNT(*) FROM silver_discussions GROUP BY source_type ORDER BY count DESC;
```

**Enrichment coverage:**
```sql
-- How many content URLs have been enriched
SELECT
  CASE WHEN enriched_at IS NOT NULL THEN 'enriched' ELSE 'not_enriched' END AS status,
  COUNT(*)
FROM silver_content
GROUP BY status;

-- Content enriched but no cross-platform discussions found
SELECT sc.canonical_url, sc.domain, sc.title
FROM silver_content sc
LEFT JOIN silver_discussions sd ON sd.content_id = sc.id
WHERE sc.enriched_at IS NOT NULL
GROUP BY sc.id
HAVING COUNT(DISTINCT sd.source_type) <= 1
LIMIT 20;
```

**Data freshness — latest collection per source:**
```sql
SELECT
  s.type,
  s.name,
  s.last_fetched_at::timestamptz AS last_fetched,
  now() - s.last_fetched_at::timestamptz AS age
FROM sources s
WHERE s.enabled = 1
ORDER BY last_fetched NULLS LAST;
```
