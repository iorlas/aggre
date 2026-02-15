# Aggre — Content Aggregation System

Aggre collects discussions from multiple sources (Hacker News, Reddit, Lobsters, RSS, YouTube, HuggingFace), fetches linked content, and discovers cross-source discussions.

## Ubiquitous Language

See `docs/semantic-model.md` for the full semantic model.

| Term | Meaning |
|------|---------|
| `BronzeDiscussion` | Immutable raw API response |
| `SilverDiscussion` | Parsed discussion thread (HN story, Reddit post, RSS entry, etc.) |
| `SilverContent` | Content artifact (article, video, paper) independent of discussions |
| `Source` | Configured data source (feed, subreddit, channel) |
| "collect" | Gather new discussions from sources (not "fetch") |
| "fetch content" | Download article/video body text |
| "enrich content discussions" | Discover cross-source discussions for known content URLs |

## Naming Conventions

- Entity names follow the Bronze/Silver medallion pattern
- Source-specific API terms (`story`, `entry`, `paper`) are OK inside collector internals only
- Database-facing code and shared interfaces must use the ubiquitous language
- Log events use dot notation: `{component}.{event}` (e.g., `hackernews.discussions_stored`)
- Variables referencing SilverDiscussion IDs should be named `discussion_id`, not `ci_id` or `post_id`

## Key Architecture

- Semantic model: `docs/semantic-model.md`
- DB models: `src/aggre/db.py`
- Collector base: `src/aggre/collectors/base.py`
- URL normalization: `src/aggre/urls.py`
- Status enums: `src/aggre/statuses.py`

## Dev Commands

- Run tests: `pytest tests/`
- Run migrations: `alembic upgrade head`
- Run collector: `aggre collect [--source=TYPE]`
