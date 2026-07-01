"""
Content enrichment — fetch external article bodies and images.

Used by market_pipeline (article body for richer AI summaries) and
render (og:image for Feishu card embedding).

Best-effort: failures here never block the pipeline.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from src.pipeline.source_constants import is_bilibili
from src.types import ScoredNewsItem
from src.tools.image_fetch import image_fetch as do_image_fetch


# ═════════════════════════════════════════════════════════════
# Article body fetch
# ═════════════════════════════════════════════════════════════

def fetch_article_body(url: str, timeout: int = 10) -> str:
    """Fetch an article URL and extract the main body text (~500 chars).

    Skips bilibili URLs since B站 content already has AI subtitles.
    Returns empty string on any failure — caller should fall back to headline.
    """
    if not url or "bilibili" in url.lower():
        return ""

    try:
        import httpx
        client = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )
        resp = client.get(url)
        resp.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Try common article content selectors
        content = ""
        for selector in ["article", ".article-content", ".news-content",
                         ".post-content", ".content", "main", ".entry-content",
                         ".Mid2L_con", ".Mid", ".news_main", ".gs-content",
                         "div.body"]:
            el = soup.select_one(selector)
            if el:
                content = el.get_text(separator="\n", strip=True)
                break

        if not content:
            body = soup.find("body")
            if body:
                content = body.get_text(separator="\n", strip=True)

        # Clean up: collapse whitespace, take first ~500 chars
        import re
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'[ \t]{2,}', ' ', content)

        # Take first meaningful chunk (avoid boilerplate headers)
        lines = [l.strip() for l in content.split("\n") if len(l.strip()) > 20]
        excerpt = " ".join(lines[:12])  # ~12 lines ≈ 500 chars

        return excerpt[:800]  # cap at 800 chars
    except Exception as e:
        print(f"  [WARN] fetch_article_body failed for {url[:80]}: {e}", file=sys.stderr)
        return ""


# ═════════════════════════════════════════════════════════════
# Image enrichment
# ═════════════════════════════════════════════════════════════

def is_image_too_small(image_url: str, min_dim: int = 200, min_kb: int = 5) -> bool:
    """Download an image and check if it's too small to be article content.

    Returns True if the image should be skipped (icon, logo, QR code, etc.).
    """
    import httpx
    import io as _io
    try:
        from PIL import Image
    except ImportError:
        return False  # can't check, assume OK

    try:
        head = httpx.head(image_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=8.0, follow_redirects=True)
        content_length = int(head.headers.get("content-length", 0))
        # If content-length header says < 3KB, definitely an icon
        if content_length and content_length < 3000:
            return True
    except Exception as e:
        print(f"  [WARN] HEAD request failed for {image_url[:80]}: {e}", file=sys.stderr)
        pass  # HEAD failed, try GET instead

    try:
        resp = httpx.get(image_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()

        file_kb = len(resp.content) / 1024
        if file_kb < min_kb:
            return True

        img = Image.open(_io.BytesIO(resp.content))
        w, h = img.width, img.height
        if w < min_dim or h < min_dim:
            return True
    except Exception as e:
        print(f"  [WARN] Image size check failed for {image_url[:80]}: {e}", file=sys.stderr)
        return False  # can't determine, assume OK

    return False


def enrich_news_images(
    news_items: list[ScoredNewsItem],
    max_fetch: int = 5,
) -> list[ScoredNewsItem]:
    """For news items without image_url, fetch og:image from article page.

    B站 items already have image_url from bilibili_videos.cover.
    Non-B站 items get their image_url filled by calling image_fetch().

    Args:
        news_items: Selected news items (already compacted).
        max_fetch: Max number of HTTP requests (avoid slowing down report).

    Returns:
        The same list, mutated in place with image_url populated.
    """
    fetched = 0
    for item in news_items:
        # Skip items that already have an image (B站 covers)
        if item.get("image_url", ""):
            continue
        # Skip bilibili items without cover (shouldn't happen, but be safe)
        if is_bilibili(item.get("source", "")):
            continue
        if fetched >= max_fetch:
            break

        url = item.get("url", "")
        if not url:
            continue

        try:
            result_json = do_image_fetch(url)
            result = json.loads(result_json)
            candidates = result.get("images", [])
            # Try each candidate: download + check actual dimensions, skip icons
            for cand in candidates[:5]:
                cand_url = cand.get("url", "")
                if not cand_url:
                    continue
                if is_image_too_small(cand_url):
                    continue
                item["image_url"] = cand_url
                fetched += 1
                break  # found a good one, stop

            # If we went through all candidates and found none, log it
            if candidates and not item.get("image_url"):
                print(f"   [image] all {len(candidates)} candidates too small for {url[:60]}",
                      file=sys.stderr)
        except Exception as e:
            print(f"   [warn] image_fetch failed for {url[:60]}: {e}", file=sys.stderr)

    return news_items


def collect_card_image_urls(
    news_items: list[ScoredNewsItem],
    max_images: int = 3,
) -> list[str]:
    """Collect image URLs from news items for Feishu card embedding.

    Priority order:
      1. track_relevant B站 video covers
      2. Regular B站 video covers
      3. News article og:image

    Args:
        news_items: Enriched news items.
        max_images: Max image URLs to return (Feishu card limit).

    Returns:
        Deduplicated list of image URLs, up to max_images.
    """
    bili_track: list[str] = []
    bili_normal: list[str] = []
    news_images: list[str] = []

    for item in news_items:
        url = item.get("image_url", "").strip()
        if not url:
            continue

        source = (item.get("source", "") or "").lower()
        is_bili = is_bilibili(source)
        is_track = bool(item.get("track_relevant"))

        if is_bili and is_track:
            if url not in bili_track:
                bili_track.append(url)
        elif is_bili:
            if url not in bili_normal:
                bili_normal.append(url)
        else:
            if url not in news_images:
                news_images.append(url)

    # Merge by priority, dedup across tiers
    result: list[str] = []
    seen: set[str] = set()
    for url in bili_track + bili_normal + news_images:
        if len(result) >= max_images:
            break
        if url not in seen:
            seen.add(url)
            result.append(url)

    return result
