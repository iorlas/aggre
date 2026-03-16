# GitHub Trending Collector — Design Spec

## Overview

Add GitHub Trending as a new content source in Aggre. The collector scrapes GitHub's trending page directly (no API, no third-party service), extracts trending repositories, and feeds them into the existing Silver pipeline. This enables daily discovery of popular repositories and cross-referencing with discussions on HN, Reddit, and Lobsters.

## Data Model Mapping

No schema changes required. GitHub Trending maps to existing tables.

### SilverContent

One row per repository URL, deduplicated by `canonical_url`:

- `canonical_url` = `https://github.com/owner/repo`
- `domain` = `github.com`
- Created once per repo via `ensure_content(conn, url)` which returns `content_id`.
- Triggers downstream pipelines (webpage fetch, discussion search).

### SilverDiscussion

One row per repo per period per time window:

| Field | Value |
|-------|-------|
| `source_type` | `"github_trending"` |
| `external_id` | `"owner/repo:daily:2026-03-16"` or `"owner/repo:weekly:2026-W11"` or `"owner/repo:monthly:2026-03"` |
| `url` | `https://github.com/owner/repo` (same as content URL — no separate discussion page, similar to RSS) |
| `title` | Repository description |
| `author` | Repository owner |
| `score` | Stars gained in period (a delta, not a total — unlike HN points or Reddit upvotes) |
| `content_id` | FK to SilverContent, obtained from `ensure_content()` |
| `published_at` | Period start date (daily = that day, weekly = Monday, monthly = 1st of month) |
| `content_text` | `null` |
| `meta` | `{"total_stars": 45000, "forks": 1200, "language": "Python", "period": "daily"}` |

### Upsert Semantics

- **Daily**: append-only. New row each day. `external_id` includes the date, so no conflicts. This is the primary discovery signal.
- **Weekly**: upsert per ISO week. If a repo stays trending, `score`, `published_at`, and `meta` update to current values.
- **Monthly**: upsert per month. Same update behavior as weekly.

### Source

One row: `type = "github_trending"`, `name = "GitHub Trending"`.

### Documentation Updates

Add `github_trending` to the semantic model (`docs/guidelines/semantic-model.md`):
- `sources.type` values list
- `meta` field semantics table
- `score` semantics table (note: delta, not total)

## Collector Implementation

### collect_discussions / process_discussion Split

Follows the standard two-phase collector pattern:

**`collect_discussions()`:**
1. Fetch HTML for each period (`daily`, `weekly`, `monthly`) — 3 HTTP requests
2. Store each raw HTML page as a bronze snapshot via `write_bronze()`
3. Parse each HTML page with selectolax, extract 25 repo dicts per page
4. Return a flat list of `DiscussionRef` objects (up to 75 total), each with:
   - `external_id` = `"owner/repo:period:time_window"`
   - `raw_data` = parsed repo dict (name, owner, description, stars, stars_in_period, forks, language, period)
   - `source_id` = from `_ensure_source("GitHub Trending")`

**`process_discussion()`:**
1. Extract fields from `raw_data`
2. Call `ensure_content(conn, f"https://github.com/{owner}/{repo}")` → get `content_id`
3. Build SilverDiscussion values dict
4. Call `_upsert_discussion(conn, values, update_columns)` — for daily, no updates needed (append-only); for weekly/monthly, update `score`, `published_at`, `meta`

### Fetching

Direct HTTP scraping of `https://github.com/trending`:

- `httpx.get("https://github.com/trending?since=daily")`
- `httpx.get("https://github.com/trending?since=weekly")`
- `httpx.get("https://github.com/trending?since=monthly")`

No authentication required. No JavaScript rendering needed — the page is server-side rendered static HTML. No anti-bot protection observed (no captcha, no 403, no JS challenges).

Each page returns exactly 25 repositories.

Rate limiting is unnecessary given the volume (3 requests per 6 hours), but a brief `time.sleep(1)` between requests is good practice to avoid bursty traffic.

### Parsing

Use **selectolax** (CSS selector-based HTML parser) to extract repo data from `<article>` elements:

- Repository name and owner
- Description
- Programming language
- Total star count
- Stars gained in period
- Fork count

**Parse failure detection:** if fewer than 10 repos are extracted from a trending page, log a warning — this likely indicates GitHub changed their HTML structure.

### Bronze Storage

Raw HTML snapshots saved per fetch using `write_bronze()`:

- `write_bronze("github_trending", "daily:2026-03-16", "page", html_content, "html")`
- `write_bronze("github_trending", "weekly:2026-W11", "page", html_content, "html")`
- `write_bronze("github_trending", "monthly:2026-03", "page", html_content, "html")`

No per-item JSON in bronze. If reprocessing is needed, re-parse the HTML snapshot.

**Note on reprocess compatibility:** the existing `reprocess_job` scans for `*/raw.json` files. GitHub Trending uses HTML snapshots, so reprocessing would require a custom scan or a source-specific reprocess method. This is acceptable — reprocessing from HTML is a future concern if needed.

### Error Handling

If one period fetch fails (e.g., weekly returns 500), log the error and continue with the remaining periods. Follow the existing pattern of per-source try/except with `continue`.

## Downstream Pipeline Behavior

### Triggered by daily collection only:

- **Webpage workflow**: fetches GitHub repo page, extracts README/description into `SilverContent.text`
- **Discussion search**: searches HN, Reddit, Lobsters for threads about the repo URL

### Weekly and monthly do NOT trigger downstream pipelines:

- SilverContent deduplication ensures the repo URL already exists from daily collection
- No value in re-searching discussions for the same URL
- Weekly/monthly exist purely for tracking trending signal persistence

The collection workflow's event emission (`_emit_item_event`) should only fire for daily period refs. Weekly/monthly refs skip event emission.

## Volume

- **Daily**: 25 rows/day
- **Weekly**: up to 25 rows/week (upsert, so no growth)
- **Monthly**: up to 25 rows/month (upsert, so no growth)
- **Total new rows**: ~25/day from daily collection. Negligible.

## Configuration

A minimal `GithubTrendingConfig` is added to `AppConfig` in `src/aggre/config.py` for consistency with the `collect_source()` contract (which calls `getattr(cfg, name)`):

```python
class GithubTrendingConfig(BaseModel):
    pass  # No configurable fields — periods are hardcoded
```

No YAML configuration needed by the user. Periods are hardcoded in the collector: `["daily", "weekly", "monthly"]`.

## Schedule

Registered in `collection.py` with a single cron schedule:

```python
("github_trending", GithubTrendingCollector, "0 */6 * * *")  # every 6 hours
```

All three periods (daily, weekly, monthly) collected in one run.

## New Files

- `src/aggre/collectors/github_trending/__init__.py`
- `src/aggre/collectors/github_trending/collector.py`
- `src/aggre/collectors/github_trending/config.py`

## New Dependencies

- `selectolax` — CSS selector-based HTML parser (new direct dependency in `pyproject.toml`)

## What This Design Does NOT Include

- **Topic/tag filtering**: GitHub Trending has no topic filter. Topic-based discovery (e.g., `github.com/topics/machine-learning`) is a separate future source.
- **Trending developers**: Only repositories, not developer profiles.
- **GitHub API enrichment**: No calls to GitHub REST/GraphQL API for additional repo metadata. Can be added later if needed.
- **Score history tracking**: Bronze HTML snapshots preserve raw data for future trend analysis. No dedicated time-series storage.
