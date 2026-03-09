"""Generic URL utilities — domain extraction and tracking parameter removal."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse

TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "msclkid",
        "ref",
        "source",
        "campaign",
        "_ga",
        "_gid",
    }
)


def extract_domain(url: str | None) -> str | None:
    """Extract the domain from a URL, stripping www. prefix."""
    if not url:
        return None
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


def strip_tracking_params(query: str) -> str:
    """Remove tracking parameters from a URL query string, sort remaining."""
    if not query:
        return ""
    params = parse_qs(query, keep_blank_values=True)
    cleaned = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
    return urlencode(sorted(cleaned.items()), doseq=True) if cleaned else ""
