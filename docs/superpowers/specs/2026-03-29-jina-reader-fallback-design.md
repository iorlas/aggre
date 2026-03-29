# Jina Reader Fallback for Webpage Downloads

**Date:** 2026-03-29
**Status:** Draft

## Problem

When direct fetch (or Browserless) and Wayback Machine both fail to retrieve a webpage, the download task raises and Hatchet retries. Some pages never succeed — they're behind JS rendering, anti-bot walls, or simply offline. We need a third-tier fallback.

## Decision

Adopt [Jina Reader](https://r.jina.ai/) as the final fallback in the download chain. Jina fetches and extracts content in one step, returning clean markdown. Since the output is already extracted text, we store it directly as `silver_content.text` — skipping the extract task entirely for the Jina path.

## Validation

Tested Jina against 7 article URLs already stored in prod (news, blogs, academic papers, technical docs). Results:

- **Articles/blogs:** Excellent. Matches or exceeds trafilatura — preserves tables, math notation, markdown formatting.
- **Reddit:** 403 Forbidden on all variants (reddit.com, old.reddit.com, www.reddit.com).
- **GitHub:** Works but noisy on listing pages (nav chrome). Fine for READMEs and blog posts.
- **HN:** Works but unnecessary (we use API).
- **Lobsters:** Works but unnecessary (we use direct scraping).

Downstream consumer of `text` is LLM processing. Markdown is a fine (arguably better) format for that use case.

## Design

### Fallback Chain

```
direct/browserless → Wayback → Jina → re-raise (Hatchet retries)
```

All within a single Hatchet attempt — no retry boundary between fallbacks.

### Where Jina Lives

In the **download task** (`_download_one` / `download_one`). Jina combines fetch and extraction, so it doesn't fit cleanly into just one of download/extract. Since it's a fetch fallback, it belongs in the download path.

When Jina succeeds:
1. Store markdown in bronze as `.md` extension (not `.html`)
2. Write markdown directly to `silver_content.text` via `update_content`
3. Return status `"downloaded_jina"`

The extract task sees `text is not None` and returns `skipped/already_done`. No changes needed to extract.

### `_fetch_via_jina` Function

```python
JINA_READER_URL = "https://r.jina.ai"

JINA_SKIP_DOMAINS = frozenset({
    "reddit.com", "old.reddit.com", "www.reddit.com",
    "news.ycombinator.com",
    "lobste.rs",
})

def _fetch_via_jina(client: httpx.Client, url: str, jina_reader_url: str) -> str | None:
    """Fetch page content via Jina Reader. Returns markdown or None."""
    try:
        resp = client.get(f"{jina_reader_url}/{url}", timeout=30.0)
        resp.raise_for_status()
        text = resp.text
        # Jina returns 200 even when target returns errors — check for empty/error content
        if not text or len(text.strip()) < 50:
            return None
        return text
    except Exception:
        logger.debug("jina.unavailable url=%s", url)
        return None
```

Same best-effort pattern as `_fetch_via_wayback` — broad except, log, return None.

Skip domains checked before calling: if the URL's domain is in `JINA_SKIP_DOMAINS`, don't attempt Jina (it will either fail or return useless content).

### No Changes to `_download_one`

`_download_one` keeps its current signature (`-> str`) and Wayback fallback logic. No modifications needed.

### Changes to `download_one`

Jina lives in `download_one`, which already has access to `engine`, `content_id`, and `row.domain`. It wraps the `_download_one` call and catches the re-raised exception:

```python
with create_http_client(...) as client:
    try:
        status = _download_one(client, row.canonical_url, row.original_url, ...)
        return StepOutput(status=status, url=row.canonical_url)
    except Exception:
        if proxy_api_url and proxy_addr:
            report_failure(proxy_api_url, proxy_addr)

        # Jina fallback — last resort after direct + Wayback both failed
        jina_reader_url = config.settings.jina_reader_url or ""
        if jina_reader_url and row.domain not in JINA_SKIP_DOMAINS:
            jina_md = _fetch_via_jina(client, row.canonical_url, jina_reader_url)
            if jina_md is not None:
                write_bronze_by_url("webpage", row.canonical_url, "response", jina_md, "md")
                update_content(engine, content_id, text=jina_md)
                return StepOutput(status="downloaded_jina", url=row.canonical_url)
        raise
```

### Settings

Add to `Settings`:

```python
jina_reader_url: str = "https://r.jina.ai"
```

Empty string disables Jina fallback (same pattern as `browserless_url`).

## What Does NOT Change

- **Extract task:** No modifications. Skips when `text is not None`.
- **Bronze read-through cache:** Still checked first. If bronze `.html` exists, returns `cached` before any fetch attempt.
- **Hatchet retry/concurrency:** Unchanged. If all three fallbacks fail, the exception propagates and Hatchet retries as before.
- **Wayback fallback:** Stays as-is, still tried before Jina.

## Risks

- **Jina free tier rate limits:** Undocumented. If we hit limits, the broad except handles it gracefully (returns None, falls through to raise).
- **Jina returns boilerplate:** Some sites include nav/footer in Jina output. Acceptable — LLM consumers handle markdown with minor chrome fine.
- **Jina service availability:** Best-effort fallback. If Jina is down, behavior is identical to today (Wayback fails → raise → Hatchet retry).
