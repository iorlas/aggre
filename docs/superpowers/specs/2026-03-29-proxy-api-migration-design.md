# Proxy API Migration: Remove Static Proxy, Use Proxy API Everywhere

**Date:** 2026-03-29
**Status:** Approved

## Summary

Remove `AGGRE_PROXY_URL` (static proxy-hub) entirely. All proxy usage migrates to the proxy API (`AGGRE_PROXY_API_URL` / `proxy-api:8080`). Each module calls `get_proxy()` / `report_failure()` directly with its own rotation strategy. No shared abstraction layer.

## Motivation

The static proxy (`proxy-hub:2080`) uses a single IP for all requests. YouTube bot-blocks 88% of downloads through it. The proxy API provides IP rotation, but only webpage downloads and Reddit comments use it today. Everything else still hits the static proxy.

## Design

### yt-dlp (`src/aggre/utils/ytdlp.py`)

Both `download_audio()` and `extract_channel_info()` change signature from `proxy_url: str` to `proxy_api_url: str`.

Retry strategy (same for both):
1. Call `get_proxy(proxy_api_url, protocol="socks5")`
2. Run yt-dlp with that proxy
3. On 403 / bot-block / transient failure: call `report_failure()`, get new proxy, retry
4. Up to 3 attempts with different IPs
5. On permanent failure (VideoUnavailableError): raise immediately, no retry

### Collectors

**Reddit** (`src/aggre/collectors/reddit/collector.py`):
- Per-request rotation: call `get_proxy()` before each HTTP request
- `report_failure()` on error
- Both discussion collection and comment fetching

**All other collectors** (HN, Lobsters, RSS, ArXiv, HuggingFace, GitHub Trending, LessWrong, YouTube):
- Per-run rotation: call `get_proxy()` once at collection start
- Use that proxy for all requests in the batch
- `report_failure()` on error
- YouTube collector passes `proxy_api_url` to `extract_channel_info()` (retry inside ytdlp.py)

**Interface change for comment fetching**: `fetch_discussion_comments()` methods on collectors currently accept `proxy_url: str | None`. Change to `proxy_api_url: str` — the workflow passes the API URL, the collector resolves its own proxy.

### Workflows

**`comments.py`**: Remove `_resolve_proxy()` helper and `_PROXY_SOURCES` set. Pass `settings.proxy_api_url` to each collector's `fetch_discussion_comments()`. Each collector handles its own proxy resolution internally.

**`webpage.py`**: Remove static fallback branch (`settings.proxy_url`). Only proxy API path remains.

**`transcription.py`**: Pass `config.settings.proxy_api_url` to `download_audio()`.

### Settings & Config Cleanup

- Remove `proxy_url: str` from `Settings` class in `settings.py`
- Remove `AGGRE_PROXY_URL` from `docker-compose.prod.yml`
- Remove `AGGRE_PROXY_URL` from `.env.example`
- Remove `AGGRE_PROXY_URL` from CI workflow (`.github/workflows/docker-publish.yml`)
- Remove from GitHub Actions secrets reference

### HTTP Client Factory

`create_http_client()` in `utils/http.py` keeps its `proxy_url: str | None` parameter unchanged. Callers resolve proxy from API and pass the URL string. No change needed here.

### Proxy API Module

`utils/proxy_api.py` stays as-is. It's already a clean thin client: `get_proxy()` and `report_failure()`.

## Testing

- Update all tests that mock/pass `proxy_url` to use `proxy_api_url`
- Test yt-dlp retry logic: mock 403 on first attempt, success on second
- Test Reddit per-request rotation: verify `get_proxy()` called per request
- Test other collectors: verify `get_proxy()` called once per run
- Test failure reporting: verify `report_failure()` called on errors
- Remove tests for static proxy fallback paths

## Files Changed

| File | Change |
|------|--------|
| `src/aggre/settings.py` | Remove `proxy_url` field |
| `src/aggre/utils/ytdlp.py` | Add retry-with-rotation, change param to `proxy_api_url` |
| `src/aggre/workflows/transcription.py` | Pass `proxy_api_url` |
| `src/aggre/workflows/comments.py` | Remove `_resolve_proxy`, pass `proxy_api_url` to collectors |
| `src/aggre/workflows/webpage.py` | Remove static fallback |
| `src/aggre/collectors/reddit/collector.py` | Per-request `get_proxy()` |
| `src/aggre/collectors/hackernews/collector.py` | Per-run `get_proxy()` |
| `src/aggre/collectors/lobsters/collector.py` | Per-run `get_proxy()` |
| `src/aggre/collectors/rss/collector.py` | Per-run `get_proxy()` |
| `src/aggre/collectors/arxiv/collector.py` | Per-run `get_proxy()` |
| `src/aggre/collectors/huggingface/collector.py` | Per-run `get_proxy()` |
| `src/aggre/collectors/github_trending/collector.py` | Per-run `get_proxy()` |
| `src/aggre/collectors/lesswrong/collector.py` | Per-run `get_proxy()` |
| `src/aggre/collectors/youtube/collector.py` | Pass `proxy_api_url` |
| `docker-compose.prod.yml` | Remove `AGGRE_PROXY_URL` |
| `.env.example` | Remove `AGGRE_PROXY_URL` |
| `.github/workflows/docker-publish.yml` | Remove `AGGRE_PROXY_URL` |
| All test files | Update proxy mocking |
