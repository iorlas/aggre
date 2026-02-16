"""Shared HTTP client factory for httpx-based collectors."""

from __future__ import annotations

import httpx

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def create_http_client(
    *,
    proxy_url: str | None = None,
    user_agent: str = BROWSER_USER_AGENT,
    timeout: float = 30.0,
    **kwargs,
) -> httpx.Client:
    """Create an httpx.Client with browser-like User-Agent and optional proxy."""
    headers = {"User-Agent": user_agent}
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))
    return httpx.Client(
        headers=headers,
        timeout=timeout,
        proxy=proxy_url,
        **kwargs,
    )
