"""Client for proxy-hub Proxy API — fetch proxy and report failures."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def get_proxy(api_url: str, protocol: str = "socks5") -> dict | None:
    """Return ``{"addr": "ip:port", "protocol": "socks5"}`` or *None*."""
    try:
        resp = httpx.get(f"{api_url}/proxy", params={"protocol": protocol}, timeout=5.0)
        if resp.status_code == 503:
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        logger.warning("proxy_api.get_proxy_failed", exc_info=True)
        return None


def report_failure(api_url: str, addr: str) -> None:
    """Best-effort failure report.  Logs but never raises."""
    try:
        httpx.post(f"{api_url}/proxy/{addr}/fail", timeout=5.0)
    except httpx.HTTPError:
        logger.debug("proxy_api.report_failure_failed", exc_info=True)
