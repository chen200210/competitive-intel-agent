"""
Web page fetcher — download and extract text content from URLs.

Caches fetched pages per URL to avoid redundant downloads.

Usage:
    from src.tools.web_fetch import web_fetch
    content = web_fetch("https://example.com/article")
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


def web_fetch(url: str, max_chars: int = 5000, **_meta: Any) -> str:
    """Fetch and extract readable text from a web page. Cached per URL.

    Args:
        url: Full URL to fetch.
        max_chars: Maximum characters to return (avoids blowing context).
        _meta: Internal kwargs injected by Agent.

    Returns:
        JSON string with {url, title, text, status, cache_hit}.
    """
    # Validate URL
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return json.dumps({"url": url, "error": "Only http/https URLs are supported"}, ensure_ascii=False)

    url_hash = hashlib.md5(url.encode()).hexdigest()

    # ── Check cache ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        cached = db.get_cached_fetch(url_hash, max_age_days=7)
        if cached is not None:
            return json.dumps({
                "url": cached["url"],
                "title": cached["title"],
                "text": cached["text"][:max_chars],
                "status": cached["status_code"],
                "cache_hit": True,
            }, ensure_ascii=False)
    except Exception:
        pass

    # ── Real fetch ──
    try:
        resp = httpx.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            },
            timeout=20.0,
            follow_redirects=True,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise elements
        for tag in soup.select("script, style, nav, footer, header, aside, .sidebar, .ad, .advertisement"):
            tag.decompose()

        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # Extract main text
        body = soup.find("body")
        text = body.get_text(separator="\n", strip=True) if body else soup.get_text(separator="\n", strip=True)

        # Collapse whitespace
        import re
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]{2,}', ' ', text)

        text_length = len(text)

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n...(truncated at {max_chars} chars, original length: {len(text)})"

        result = {
            "url": str(resp.url),
            "title": title,
            "text": text,
            "status": resp.status_code,
            "cache_hit": False,
        }

        # ── Write cache ──
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            db.cache_fetch(
                url_hash=url_hash,
                url=url,
                title=title,
                text=text,
                text_length=text_length,
                status_code=resp.status_code,
            )
        except Exception:
            pass

        return json.dumps(result, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        return json.dumps({"url": url, "error": f"HTTP {e.response.status_code}", "text": "", "cache_hit": False}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"url": url, "error": str(e), "text": "", "cache_hit": False}, ensure_ascii=False)


# ── Tool descriptor ───────────────────────────────────────────

TOOL_DESCRIPTOR: dict[str, Any] = {
    "name": "web_fetch",
    "description": (
        "Fetch and extract the main text content from a web page. "
        "Use this to read full articles, announcements, or game update notes "
        "after finding a relevant URL via web_search."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Full URL of the page to fetch (must start with http:// or https://)",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default 5000)",
            },
        },
        "required": ["url"],
    },
}
