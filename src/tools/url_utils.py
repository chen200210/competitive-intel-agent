"""Shared URL utilities — domain extraction, normalization, etc."""

from __future__ import annotations

import re


def extract_domain(url: str) -> str:
    """Extract domain name from URL for display.

    >>> extract_domain("https://www.36kr.com/article/123")
    '36kr.com'
    >>> extract_domain("https://example.com/path?q=1")
    'example.com'
    >>> extract_domain("")
    ''
    """
    if not url:
        return ""
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else ""
