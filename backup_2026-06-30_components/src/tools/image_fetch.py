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

        # Fallback: find first <img> with reasonable size, skip ads/qr/logos
        if not images:
            candidate_imgs = []
            for img in soup.find_all("img"):
                src = img.get("src", "")
                if not src:
                    continue
                img_url = urljoin(url, src)
                if img_url in seen or not _is_valid_image_url(img_url):
                    continue

                # Skip images that are too small (likely icons, QR codes, ads)
                w = _parse_dimension(img.get("width"))
                h = _parse_dimension(img.get("height"))
                if (w is not None and w < 200) or (h is not None and h < 200):
                    continue

                # Check parent context: prefer images in article/content areas
                parent_score = _parent_context_score(img)

                seen.add(img_url)
                candidate_imgs.append({
                    "url": img_url,
                    "type": "img_tag",
                    "width": str(w) if w else "",
                    "height": str(h) if h else "",
                    "parent_score": parent_score,
                })

            # Sort by parent score (article content first), then take top 3
            candidate_imgs.sort(key=lambda x: x.get("parent_score", 0), reverse=True)
            for ci in candidate_imgs[:3]:
                images.append({
                    "url": ci["url"],
                    "type": ci["type"],
                    "width": ci.get("width", ""),
                    "height": ci.get("height", ""),
                })

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
    """Basic check that a URL looks like an image (not a logo/icon/tracker/ad/qr)."""
    if not url.startswith(("http://", "https://")):
        return False
    skip_patterns = [
        # Tracking / tiny
        "1x1", "pixel", "tracking", "favicon", "beacon",
        # Site logos & icons
        "/logo.", "/logo-", "logo.png", "logo.jpg", "logo.svg", "logo.webp",
        "/icon.", "icon.png", "icon.jpg",
        "apple-touch-icon",
        "avatar", "default-avatar",
        # Ads & QR codes
        "qr.png", "qr.jpg", "qrcode", "qr_code", "qr-code",
        "banner-ad", "banner_ad", "/ad.", "/ad-",
        "广告", "二维码",
        # Analytics / beacons
        "analytics", "pixel", "spacer",
    ]
    url_lower = url.lower()
    return not any(p in url_lower for p in skip_patterns)


def _parse_dimension(val: Any) -> int | None:
    """Parse width/height attribute value to integer, handling '100px' or '50%'."""
    if val is None:
        return None
    try:
        if isinstance(val, (int, float)):
            return int(val)
        s = str(val).strip().rstrip("px").rstrip("%")
        if s:
            return int(float(s))
    except (ValueError, TypeError):
        pass
    return None


def _parent_context_score(img_tag: Any) -> int:
    """Score an <img> by how likely it's inside article content vs sidebar/ad.

    Higher = article image.  Lower = ad / QR code / decoration.
    """
    score = 0
    parent = img_tag.parent
    depth = 0
    while parent is not None and depth < 6:
        depth += 1
        tag_name = (getattr(parent, 'name', '') or '').lower()
        classes = ' '.join(getattr(parent, 'attrs', {}).get('class', [])) if hasattr(parent, 'attrs') else ''
        parent_id = getattr(parent, 'attrs', {}).get('id', '') if hasattr(parent, 'attrs') else ''

        combined = f"{tag_name} {classes} {parent_id}".lower()

        # Strong positive: article content area
        if any(kw in combined for kw in ('article', 'content', 'detail', 'post', 'entry', 'main')):
            score += 3
        # Weak positive: text area
        if any(kw in combined for kw in ('text', 'body', 'desc', 'para')):
            score += 1
        # Negative: sidebar, footer, ad, widget
        if any(kw in combined for kw in ('sidebar', 'footer', 'ad-', 'ad_', '-ad', '_ad',
                                          'widget', 'recommend', 'related')):
            score -= 3
        # Strong negative: QR code area
        if any(kw in combined for kw in ('qr', 'qrcode', 'code')):
            score -= 5

        parent = parent.parent if hasattr(parent, 'parent') else None

    return score


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
