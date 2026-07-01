"""
PocketGamer.biz News Scraper — mobile gaming industry news via RSS.

Source: https://www.pocketgamer.biz/index.rss (RSS 2.0, ~50 items, ~7 days)
Output: Direct to market_news DB table — no CSV intermediate needed.

Each RSS item includes: title, link, guid, description (excerpt), pubDate,
category (News|Features|Industry Voices|Data), and image (media:content).

Usage:
    python -m tools.scrapers.pocketgamer_biz
    python -m tools.scrapers.pocketgamer_biz --date 2026-06-24
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

# Fix import path for running as script or module
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.pipeline.source_constants import NewsSource

# ── Config ───────────────────────────────────────────────────────
RSS_URL = "https://www.pocketgamer.biz/index.rss"
FETCH_TIMEOUT = 20  # seconds
MAX_ITEMS = 30  # max items to process per run


def run_scrape(date: str | None = None) -> int:
    """Fetch PocketGamer.biz RSS, filter by date, sync to market_news.

    Args:
        date: Target date YYYY-MM-DD. Defaults to today.

    Returns:
        Number of new items inserted into market_news.
    """
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    valid_dates = {target_date, yesterday}

    print(f"── PocketGamer.biz RSS (pocketgamer.biz/index.rss) ──")

    # ═══ Fetch RSS ═══
    items = _fetch_rss()
    if not items:
        print("  [WARN] No items in RSS feed")
        return 0
    print(f"  RSS feed: {len(items)} items")

    # ═══ Filter by date ═══
    filtered = _filter_by_date(items, valid_dates)
    if not filtered:
        print(f"  No items for dates {valid_dates}")
        return 0
    print(f"  Date filter ({target_date} / {yesterday}): {len(filtered)} items")

    # ═══ Deduplicate & sync to DB ═══
    count = _sync_to_db(filtered, target_date)
    return count


def _fetch_rss() -> list[dict[str, Any]]:
    """Fetch and parse the PocketGamer.biz RSS feed.

    Returns list of dicts with keys:
        headline, url, guid, excerpt, publish_date, category, image_url
    """
    try:
        client = httpx.Client(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/rss+xml, application/xml, text/xml;q=0.9,*/*;q=0.8",
            },
            timeout=httpx.Timeout(FETCH_TIMEOUT),
            follow_redirects=True,
        )
        resp = client.get(RSS_URL)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ERROR] RSS fetch failed: {e}")
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        print(f"  [ERROR] RSS XML parse failed: {e}")
        return []

    channel = root.find("channel")
    if channel is None:
        print("  [ERROR] No <channel> in RSS feed")
        return []

    items: list[dict[str, Any]] = []
    # XML namespaces
    ns = {"media": "http://search.yahoo.com/mrss/"}

    for item_el in channel.findall("item"):
        try:
            title = _text(item_el, "title")
            link = _text(item_el, "link")
            guid = _text(item_el, "guid")
            description = _text(item_el, "description")
            pub_date = _text(item_el, "pubDate")
            category = _text(item_el, "category")

            # Image from media:content
            image_url = ""
            media_content = item_el.find("media:content", ns)
            if media_content is not None:
                image_url = media_content.get("url", "")
            # Fallback: enclosure
            if not image_url:
                enclosure = item_el.find("enclosure")
                if enclosure is not None:
                    image_url = enclosure.get("url", "")

            if not title or not link:
                continue

            # Use guid if available, fall back to link
            item_id = guid or link

            # Clean excerpt: strip HTML tags, remove "... [MORE]" trailer
            excerpt = _clean_excerpt(description)

            # Parse publish date to YYYY-MM-DD
            publish_date = _parse_pubdate(pub_date)

            items.append({
                "headline": title.strip(),
                "url": link.strip(),
                "guid": item_id.strip(),
                "excerpt": excerpt,
                "publish_date": publish_date,
                "category": category.strip() if category else "News",
                "image_url": image_url.strip(),
            })

            if len(items) >= MAX_ITEMS:
                break

        except Exception as e:
            print(f"  [WARN] Skipping malformed RSS item: {e}")
            continue

    return items


def _filter_by_date(
    items: list[dict[str, Any]], valid_dates: set[str]
) -> list[dict[str, Any]]:
    """Keep only items whose publish_date is in valid_dates."""
    filtered: list[dict[str, Any]] = []
    for item in items:
        pd = item.get("publish_date", "")
        if pd in valid_dates:
            filtered.append(item)
    return filtered


def _sync_to_db(items: list[dict[str, Any]], date: str) -> int:
    """Map RSS items to record dicts, insert with cross-day URL dedup."""
    try:
        from src.storage.sqlite import get_db
        db = get_db()

        # ── Map RSS items → standard record dicts ──
        records: list[dict[str, Any]] = []
        for item in items:
            url = item.get("url", "")
            if not url:
                continue
            records.append({
                "date": date,
                "headline": item["headline"],
                "source": NewsSource.POCKET_GAMER,
                "url": url,
                "category": item.get("category", "News"),
                "related_game": "",
                "track_relevant": False,
                "publish_date": item.get("publish_date", ""),
            })

        # ── Insert with cross-day URL dedup ──
        return db.insert_market_news_deduped(records, date)

    except Exception as e:
        print(f"  [WARN] DB sync failed: {e}")
        return 0


# ── Helpers ──────────────────────────────────────────────────────


def _text(element: ET.Element, tag: str) -> str:
    """Extract text content of a child element, or empty string."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text
    return ""


def _clean_excerpt(raw: str) -> str:
    """Clean RSS description: strip HTML, remove truncation trailer.

    Input:  CDATA with HTML like "<p>Supernatural RPG ... [<a href="...">MORE</a>]</p>"
    Output: "Supernatural RPG ..."
    """
    if not raw:
        return ""

    # Strip HTML tags — keep text content only
    text = re.sub(r'<[^>]+>', ' ', raw)
    # Remove "... [ MORE ]" style truncation trailers
    text = re.sub(r'\.{2,}?\s*\[?\s*MORE\s*\]?\s*$', '', text)
    text = re.sub(r'\[<a[^>]*>MORE</a>\]', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _parse_pubdate(pubdate_str: str) -> str:
    """Parse RFC 2822 pubDate to YYYY-MM-DD.

    Input:  "Tue, 23 Jun 2026 12:40:00 +0100"
    Output: "2026-06-23"
    """
    if not pubdate_str:
        return ""

    # Month name → number
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    # Pattern: "Tue, 23 Jun 2026 12:40:00 +0100"
    m = re.match(
        r'\w{3},\s+(\d{1,2})\s+(\w{3})\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})',
        pubdate_str.strip(),
    )
    if m:
        day = int(m.group(1))
        month_str = m.group(2).lower()[:3]
        year = int(m.group(3))
        month = months.get(month_str, 1)
        return f"{year}-{month:02d}-{day:02d}"

    # Fallback: try ISO 8601 "2026-06-23T12:40:00+01:00"
    m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})T', pubdate_str.strip())
    if m2:
        return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"

    return ""


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser(description="PocketGamer.biz News Scraper")
    parser.add_argument("--date", type=str, default=None, help="Date YYYY-MM-DD")
    args = parser.parse_args()

    count = run_scrape(date=args.date)

    if count > 0:
        print(f"\n  [OK] {count} new PocketGamer.biz articles synced")
    else:
        print("\n  [info] No new PocketGamer.biz articles")
