from __future__ import annotations

import httpx
import pytest
import respx

from aggre.utils.proxy_api import get_proxy, report_failure

pytestmark = pytest.mark.unit


def test_get_proxy_returns_info() -> None:
    with respx.mock:
        respx.get("http://api:8080/proxy").mock(return_value=httpx.Response(200, json={"addr": "1.2.3.4:8080", "protocol": "socks5"}))
        result = get_proxy("http://api:8080", protocol="socks5")
        assert result == {"addr": "1.2.3.4:8080", "protocol": "socks5"}


def test_get_proxy_503_returns_none() -> None:
    with respx.mock:
        respx.get("http://api:8080/proxy").mock(return_value=httpx.Response(503, json={"error": "no_proxy_available"}))
        result = get_proxy("http://api:8080")
        assert result is None


def test_get_proxy_connection_error_returns_none() -> None:
    with respx.mock:
        respx.get("http://api:8080/proxy").mock(side_effect=httpx.ConnectError("refused"))
        result = get_proxy("http://api:8080")
        assert result is None


def test_report_failure_swallows_errors() -> None:
    with respx.mock:
        respx.post("http://api:8080/proxy/1.2.3.4:8080/fail").mock(side_effect=httpx.ConnectError("refused"))
        # Should not raise
        report_failure("http://api:8080", "1.2.3.4:8080")
