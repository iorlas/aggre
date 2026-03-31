# 006: Proxy API over static proxy configuration

**Status:** Active
**Date:** 2026-03-29

## Why
Static proxy configuration (`AGGRE_PROXY_URL`) couldn't support proxy rotation, per-domain proxy selection, or automatic failover. Reddit comment fetching needed rotating proxies to avoid rate limits.

## Decision
All HTTP requests route through a proxy API service (`AGGRE_PROXY_API_URL`). The API handles proxy selection, rotation, and failover internally. Collectors and workflows call `get_proxy()` from `utils/proxy_api.py` instead of reading a static URL.

## Not chosen
- Static proxy with multiple URLs in config -- requires client-side rotation logic in every collector
- No proxy (direct requests) -- rate-limited by source APIs, IP bans on high-volume fetching
- Proxy middleware in the application -- mixes infrastructure concern with business logic

## Consequence
All HTTP-making code depends on the proxy API service being available. `AGGRE_PROXY_API_URL` replaces `AGGRE_PROXY_URL` in settings.
