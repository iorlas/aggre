# GitHub Trending Collector — Design Spec

## Overview

Add GitHub Trending as a new content source in Aggre. The collector scrapes GitHub's trending page directly (no API, no third-party service), extracts trending repositories, and feeds them into the existing Silver pipeline. This enables daily discovery of popular repositories and cross-referencing with discussions on HN, Reddit, and Lobsters.

## Data Model Mapping

No schema changes required. GitHub Trending maps to existing tables.

### SilverContent

One row per repository URL, deduplicated by `canonical_url`:

- `canonical_url` = `https://github.com/owner/repo`
- `domain` = `github.com`
- Created once per repo. Triggers downstream pipelines (webpage fetch, discussion search).

### SilverDiscussion

One row per repo per period per time window:

| Field | Value |
|-------|-------|
| `source_type` | `"github_trending"` |
| `external_id` | `"owner/repo:daily:2026-03-16"` or `"owner/repo:weekly:2026-W11"` or `"owner/repo:monthly:2026-03"` |
| `url` | `https://github.com/owner/repo` |
| `title` | Repository description |
| `author` | Repository owner |
| `score` | Stars gained in period |
| `published_at` | Period start date (daily = that day, weekly = Monday, monthly = 1st of month) |
| `content_text` | `null` |
| `meta` | `{"total_stars": 45000, "forks": 1200, "language": "Python", "period": "daily"}` |

### Upsert Semantics

- **Daily**: append-only. New row each day. `external_id` includes the date, so no conflicts. This is the primary discovery signal.
- **Weekly**: upsert per ISO week. If a repo stays trending, `score` and `published_at` update to current week's values.
- **Monthly**: upsert per month. Same update behavior as weekly.

### Source

One row: `type = "github_trending"`, `name = "GitHub Trending"`.

## Collection Approach

### Fetching

Direct HTTP scraping of `https://github.com/trending`:

- `httpx.get("https://github.com/trending?since=daily")`
- `httpx.get("https://github.com/trending?since=weekly")`
- `httpx.get("https://github.com/trending?since=monthly")`

No authentication required. No JavaScript rendering needed — the page is server-side rendered static HTML. No anti-bot protection observed (no captcha, no 403, no JS challenges).

Each page returns exactly 25 repositories.

### Parsing

Use **selectolax** (CSS selector-based HTML parser) to extract repo data from `<article>` elements:

- Repository name and owner
- Description
- Programming language
- Total star count
- Stars gained in period
- Fork count

### Bronze Storage

Raw HTML snapshots saved per fetch:

- `github_trending/daily/2026-03-16/page.html`
- `github_trending/weekly/2026-W11/page.html`
- `github_trending/monthly/2026-03/page.html`

No per-item JSON in bronze. If reprocessing is needed, re-parse the HTML snapshot.

## Downstream Pipeline Behavior

### Triggered by daily collection only:

- **Webpage workflow**: fetches GitHub repo page, extracts README/description into `SilverContent.text`
- **Discussion search**: searches HN, Reddit, Lobsters for threads about the repo URL

### Weekly and monthly do NOT trigger downstream pipelines:

- SilverContent deduplication ensures the repo URL already exists from daily collection
- No value in re-searching discussions for the same URL
- Weekly/monthly exist purely for tracking trending signal persistence

## Volume

- **Daily**: 25 rows/day
- **Weekly**: up to 25 rows/week (upsert, so no growth)
- **Monthly**: up to 25 rows/month (upsert, so no growth)
- **Total new rows**: ~25/day from daily collection. Negligible.

## Configuration

No YAML configuration needed. Periods are hardcoded in the collector: `["daily", "weekly", "monthly"]`. There is only one trending page — no configurable sources like subreddits or channels.

## Schedule

Registered in `collection.py` with a single cron schedule:

```python
("github_trending", GithubTrendingCollector, "0 */6 * * *")  # every 6 hours
```

All three periods (daily, weekly, monthly) collected in one run.

## New Files

- `src/aggre/collectors/github_trending/__init__.py`
- `src/aggre/collectors/github_trending/collector.py`

## New Dependencies

- `selectolax` — CSS selector-based HTML parser

## What This Design Does NOT Include

- **Topic/tag filtering**: GitHub Trending has no topic filter. Topic-based discovery (e.g., `github.com/topics/machine-learning`) is a separate future source.
- **Trending developers**: Only repositories, not developer profiles.
- **GitHub API enrichment**: No calls to GitHub REST/GraphQL API for additional repo metadata. Can be added later if needed.
- **Score history tracking**: Bronze HTML snapshots preserve raw data for future trend analysis. No dedicated time-series storage.
