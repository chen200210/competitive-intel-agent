"""
Image fetcher — extract main images from web pages.

Extracts og:image, twitter:image, and other meta tags from HTML.
Useful for getting App Store screenshots, TapTap icons, etc.

Usage:
    from src.tools.image_fetch import image_fetch
    result = image_fetch("https://apps.apple.com/cn/app/id123456")
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def image_fetch(url: str, **_meta: Any) -> str:
    """Extract main images from a web page.

    Args:
        url: Page URL (App Store, TapTap, etc.).
        _meta: Internal kwargs injected by Agent.

    Returns:
        JSON string with {url, title, images: [{url, width, height, type}]}.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return json.dumps({"url": url, "error": "Only http/https URLs are supported"}, ensure_ascii=False)

    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": UA},
            timeout=15.0,
            follow_redirects=True,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        images: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Priority order: og:image → twitter:image → apple-touch-icon → first large img
        for meta in soup.find_all("meta"):
            prop = meta.get("property", "") or meta.get("name", "")
            content = meta.get("content", "")
            if not content:
                continue

            if prop in ("og:image", "twitter:image"):
                img_url = urljoin(url, content)
                if img_url not in seen and _is_valid_image_url(img_url):
                    seen.add(img_url)
                    images.append({
                        "url": img_url,
                        "type": prop.replace("og:", "").replace("twitter:", ""),
                        "width": meta.get("content:width", ""),
                        "height": meta.get("content:height", ""),
                    })
            elif prop == "apple-touch-icon":
                img_url = urljoin(url, content)
                if img_url not in seen:
                    seen.add(img_url)
                    images.append({"url": img_url, "type": "icon"})

        # Fallback: find first <img> with reasonable size
        if not images:
            for img in soup.find_all("img"):
                src = img.get("src", "")
                if src:
                    img_url = urljoin(url, src)
                    if img_url not in seen and _is_valid_image_url(img_url):
                        seen.add(img_url)
                        images.append({"url": img_url, "type": "img_tag"})
                        if len(images) >= 3:
                            break

        return json.dumps({
            "url": str(resp.url),
            "title": title,
            "images": images[:5],
        }, ensure_ascii=False)

    except httpx.HTTPStatusError as e:
        return json.dumps({"url": url, "error": f"HTTP {e.response.status_code}", "images": []}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"url": url, "error": str(e), "images": []}, ensure_ascii=False)


def _is_valid_image_url(url: str) -> bool:
    """Basic check that a URL looks like an image."""
    if not url.startswith(("http://", "https://")):
        return False
    # Skip tracking pixels and tiny icons
    skip_patterns = ["1x1", "pixel", "tracking", "favicon", "beacon"]
    url_lower = url.lower()
    return not any(p in url_lower for p in skip_patterns)


# ── Tool descriptor for Agent registration ──────────────────────

TOOL_DESCRIPTOR: dict[str, Any] = {
    "name": "image_fetch",
    "description": (
        "Extract main images from a web page (App Store, TapTap, etc.). "
        "Returns up to 5 image URLs found via og:image, twitter:image, "
        "or img tags. Use this to get game screenshots and icons."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Page URL to extract images from",
            },
        },
        "required": ["url"],
    },
}


# ── CLI test ────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    url_arg = sys.argv[1] if len(sys.argv) > 1 else "https://apps.apple.com/cn/app/id6445914468"
    print(image_fetch(url_arg))
