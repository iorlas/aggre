# New Collectors: Continuation Notes

Status: **Phase 1 complete, Phase 2 in progress**

## What's Done

### Phase 1: Refactor Collection Jobs ✅

Monolithic `collection/job.py` and `collection/schedule.py` split into per-source modules.

**Created files:**
- `src/aggre/dagster_defs/collection/_shared.py` — `collect_source()` helper, `_RETRY` policy
- `src/aggre/dagster_defs/collection/{youtube,reddit,hackernews,lobsters,rss,huggingface,telegram}.py` — each has op + job + schedule
- `src/aggre/dagster_defs/collection/__init__.py` — registry re-exports for dagster_defs

**Deleted files:**
- `src/aggre/dagster_defs/collection/job.py`
- `src/aggre/dagster_defs/collection/schedule.py`

**Modified files:**
- `src/aggre/dagster_defs/__init__.py` — imports from collection package
- `tests/test_orchestration.py` — import path updated to `_shared`

**Post-refactor user changes:** All per-source ops now use `app_config` Dagster resource instead of `load_config()`. The `load_config` import was removed from per-source files.

**Verification:** lint passes, 322 tests pass, dagster definitions validate passes.

### Phase 2: Partially Started

**ArXiv collector — files created, not wired:**
- `src/aggre/collectors/arxiv/__init__.py`
- `src/aggre/collectors/arxiv/config.py` — `ArxivSource`, `ArxivConfig`
- `src/aggre/collectors/arxiv/collector.py` — `ArxivCollector` using feedparser
- `src/aggre/dagster_defs/collection/arxiv.py` — op + job + schedule (6h)

**LessWrong collector — files created, not wired:**
- `src/aggre/collectors/lesswrong/__init__.py`
- `src/aggre/collectors/lesswrong/config.py` — `LesswrongSource`, `LesswrongConfig`
- `src/aggre/collectors/lesswrong/collector.py` — `LesswrongCollector` using GraphQL
- `src/aggre/dagster_defs/collection/lesswrong.py` — op + job + schedule (3h)

**GitHub Trending collector — not started.**

## What Remains

### Phase 2 TODO

1. **Review ArXiv collector** — verify it follows the updated `app_config` resource pattern (the dagster file still uses `load_config()`, needs updating to match the user's refactor of other per-source files)
2. **Review LessWrong collector** — same `app_config` pattern check
3. **Create GitHub Trending collector** — config, collector (BS4 + browserless), dagster file. Add `beautifulsoup4>=4.12` to pyproject.toml
4. **Wire all three into config/registry/dagster:**
   - `src/aggre/config.py` — add `arxiv: ArxivConfig`, `github_trending: GithubTrendingConfig`, `lesswrong: LesswrongConfig` fields
   - `src/aggre/collectors/__init__.py` — add 3 entries to `COLLECTORS` dict
   - `src/aggre/settings.py` — add `github_trending_rate_limit: float = 2.0`, `lesswrong_rate_limit: float = 1.0`
   - `src/aggre/dagster_defs/collection/__init__.py` — add re-exports for 3 new jobs + schedules
   - `src/aggre/dagster_defs/__init__.py` — add 3 jobs + 3 schedules to `dg.Definitions`
   - `config.yaml` — add `arxiv`, `github_trending`, `lesswrong` sections
5. **Contract tests** — VCR cassettes for ArXiv RSS, GitHub Trending HTML, LessWrong GraphQL
6. **Verification** — lint, test-e2e, dagster validate

### Key Design Decisions (from approved plan)

- **ArXiv**: feedparser on `http://export.arxiv.org/rss/{category}`, paper ID from URL regex, abstract in `content_text`, 6h schedule
- **LessWrong**: GraphQL POST to `https://www.lesswrong.com/graphql`, filter by `baseScore >= min_karma`, link posts vs native essays, 3h schedule
- **GitHub Trending**: HTML scrape via browserless + BeautifulSoup, `article.Box-row` parsing, skip silently if no browserless_url, 6h schedule
- **ArXiv URL dedup**: `normalize_url()` already handles arxiv.org (strips version suffix), so `ensure_content` deduplicates automatically
