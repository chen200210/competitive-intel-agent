"""
Hot topic tracking — Phase 0.5 (keyword collection) + Phase 1.5 (news search).

Phase 0.5: Collect hot keywords from Baidu/Zhihu + curated interests,
           apply feedback-based weight adjustment → hot_keywords table.
Phase 1.5: Search game-related news for each keyword (360-news first,
           fallback Sogou-news) → hot_topic_news table.

Usage:
    from src.pipeline.hot_tracker import collect_hot_keywords, search_hot_topics

    kw_result = collect_hot_keywords("2026-06-25")
    hot_result = search_hot_topics("2026-06-25")
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date
from typing import Any

import httpx

from src.types import HotTopicItem
from src.pipeline.token_utils import headline_dedup_tokens
from bs4 import BeautifulSoup
from pydantic import BaseModel

from src.storage.sqlite import get_db
from src.agents.base import Agent, Tool
from src.tools.web_search import _scrape_360_news, _scrape_sogou_news

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ── Curated persistent-interest keywords (game industry insider lens) ──
CURATED_KEYWORDS: list[dict[str, Any]] = [
    {"keyword": "AI技术 游戏", "source": "curated", "rank": 1},
    {"keyword": "游戏引擎", "source": "curated", "rank": 2},
    {"keyword": "游戏硬件 变革", "source": "curated", "rank": 3},
    {"keyword": "游戏版号 审核", "source": "curated", "rank": 4},
    {"keyword": "大厂动态 游戏", "source": "curated", "rank": 5},
]

# Game/tech relevance signals for filtering hot topics
GAME_SIGNALS: list[str] = [
    "游戏", "电竞", "手游", "端游", "主机", "Steam", "steam",
    "Epic", "Unreal", "Unity", "引擎", "版号", "审核", "审批",
    "腾讯", "网易", "米哈游", "育碧", "任天堂", "索尼", "微软",
    "Xbox", "PlayStation", "Nintendo", "GDC", "E3", "TGA",
    "AI", "人工智能", "GPU", "显卡", "芯片", "硬件",
    "独立游戏", "3A", "开放世界", "RPG", "FPS", "MOBA",
    "电竞", "赛事", "战队", "直播",
]

# Non-game signals to filter out
NON_GAME_SIGNALS: list[str] = [
    "股票", "基金", "楼市", "房价", "比特币", "NFT",
    "综艺", "电视剧", "电影票房", "明星", "绯闻",
    "足球", "篮球", "NBA", "世界杯", "奥运会",
    "疫情", "地震", "台风",
]


# ═══════════════════════════════════════════════════════════════════
# Phase 0.5: Hot Keyword Collection
# ═══════════════════════════════════════════════════════════════════

def collect_hot_keywords(date: str) -> dict[str, Any]:
    """Collect hot keywords from multiple sources → hot_keywords table.

    Sources: Baidu hot search, Zhihu hot list, Weibo hot search,
    Xiaohongshu trending (via search aggregation), curated interest areas.
    Applies feedback-based weight adjustment from user_feedback (hot_click).

    Returns:
        {"keywords": [...], "sources": [...], "count": N}
    """
    all_keywords: list[dict[str, Any]] = []
    sources_used: list[str] = []

    # ── Parallel fetch from all external sources ──
    # Each source is an independent HTTP call — run them concurrently
    # to avoid serial timeout stacking (4 × 15s worst case → ~15s).
    _SOURCES: list[tuple[str, Any]] = [
        ("baidu", _fetch_baidu_hotspots),
        ("zhihu", _fetch_zhihu_hotspots),
        ("weibo", _fetch_weibo_hotspots),
        ("xiaohongshu", _fetch_xiaohongshu_hotspots),
    ]

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(fn): name for name, fn in _SOURCES}
        for f in as_completed(futures):
            name = futures[f]
            try:
                result = f.result()
                if result:
                    all_keywords.extend(result)
                    sources_used.append(name)
            except Exception as e:
                print(f"  [WARN] {name} hotspot scrape failed: {e}", file=sys.stderr)

    # ── Source 5: Curated interests (always available) ──
    # Deep-copy each dict to avoid mutating the module-level constant
    all_keywords.extend(dict(kw) for kw in CURATED_KEYWORDS)
    sources_used.append("curated")

    if not all_keywords:
        return {"keywords": [], "sources": [], "count": 0}

    # ── Dedup + merge by keyword ──
    merged: dict[str, dict[str, Any]] = {}
    for kw in all_keywords:
        key = kw["keyword"]
        if key in merged:
            # Keep the better rank
            if kw.get("rank", 99) < merged[key].get("rank", 99):
                merged[key] = kw
        else:
            merged[key] = kw

    # ── Filter for game/tech relevance ──
    relevant: dict[str, dict[str, Any]] = {}
    for key, kw in merged.items():
        if _is_game_relevant(key):
            relevant[key] = kw

    # If nothing relevant after filtering, keep curated at minimum
    if not relevant:
        for kw in CURATED_KEYWORDS:
            relevant[kw["keyword"]] = kw

    # ── Apply feedback-based weight adjustment ──
    db = get_db()
    click_stats: dict[str, int] = {}
    try:
        click_stats = db.get_hot_keyword_click_stats(days=14)
    except Exception as e:
        print(f"  [WARN] get_hot_keyword_click_stats failed: {e}", file=sys.stderr)

    keywords: list[dict[str, Any]] = []
    for key, kw in relevant.items():
        base_weight = max(0.1, 1.0 - (kw.get("rank", 50) / 100.0))
        clicks = click_stats.get(key, 0)
        adjusted = round(base_weight + (clicks * 0.2), 2)
        kw["weight"] = adjusted
        keywords.append(kw)

    # Sort by weight desc
    keywords.sort(key=lambda x: x.get("weight", 0), reverse=True)

    # ── Diversity guarantee: curated keywords get minimum slots ──
    # Without this, low-click curated topics get permanently squeezed out
    # by high-frequency trending topics, and the pool slowly collapses.
    CURATED_MIN_SLOTS = 3
    MAX_TOTAL = 10

    curated = [kw for kw in keywords if kw.get("source") == "curated"]
    non_curated = [kw for kw in keywords if kw.get("source") != "curated"]

    # Take top non-curated first, then backfill curated to hit the minimum
    top_non_curated = non_curated[:MAX_TOTAL - CURATED_MIN_SLOTS]
    top_curated_by_weight = curated[:CURATED_MIN_SLOTS]

    # Merge: if curated already appear in the weight-sorted top, don't double-count
    top_curated_keys = {kw["keyword"] for kw in top_curated_by_weight}
    # Non-curated top picks (up to 7 slots)
    picked = list(top_non_curated)
    # Fill remaining slots with curated that didn't make the weight cut
    for kw in curated:
        if kw["keyword"] not in {p["keyword"] for p in picked}:
            if len(picked) < MAX_TOTAL:
                picked.append(kw)

    # If still under MAX_TOTAL (e.g., few sources returned), fill from non-curated
    for kw in non_curated:
        if kw["keyword"] not in {p["keyword"] for p in picked}:
            if len(picked) < MAX_TOTAL:
                picked.append(kw)

    keywords = picked[:MAX_TOTAL]

    # ── Write to DB ──
    try:
        records = [
            {
                "date": date,
                "keyword": kw["keyword"],
                "source": kw.get("source", "curated"),
                "rank": kw.get("rank", 99),
                "weight": kw.get("weight", 1.0),
            }
            for kw in keywords
        ]
        db.insert_hot_keywords(records)
    except Exception as e:
        print(f"  [WARN] Failed to write hot keywords to DB: {e}", file=sys.stderr)

    return {
        "keywords": keywords,
        "sources": sources_used,
        "count": len(keywords),
    }


def _fetch_baidu_hotspots() -> list[dict[str, Any]]:
    """Scrape Baidu hot search board for game/tech-relevant topics."""
    try:
        resp = httpx.get(
            "https://top.baidu.com/board?tab=realtime",
            headers={"User-Agent": UA},
            timeout=15.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] Baidu hotspot scrape failed: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, Any]] = []

    # Try multiple selectors (Baidu may change HTML structure)
    items = soup.select(".category-wrap_iQLoo, .content_1YWBm, .hot-item, .item-wrap")
    if not items:
        # Fallback: look for any link-like elements with title text
        items = soup.select("a[href*='baidu.com']")

    for idx, item in enumerate(items[:30]):
        title_el = (
            item.select_one(".title_dIF3B, .c-single-text-ellipsis, .title")
            or item.select_one("a")
        )
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 2:
            continue

        # Skip non-game/tech topics
        if not _is_game_relevant(title):
            continue

        results.append({
            "keyword": _normalize_keyword(title),
            "source": "baidu",
            "rank": idx + 1,
        })

    return results


def _fetch_zhihu_hotspots() -> list[dict[str, Any]]:
    """Scrape Zhihu hot list for game/tech topics."""
    try:
        resp = httpx.get(
            "https://www.zhihu.com/hot",
            headers={"User-Agent": UA},
            timeout=15.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] Zhihu hotspot scrape failed: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, Any]] = []

    items = soup.select(".HotList-item, .HotItem, .List-item")
    if not items:
        items = soup.select("[data-za-detail-view-path-module]")

    for idx, item in enumerate(items[:30]):
        title_el = (
            item.select_one(".HotList-itemTitle, h2, h3")
            or item.select_one("a")
        )
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title or len(title) < 2:
            continue
        if not _is_game_relevant(title):
            continue

        results.append({
            "keyword": _normalize_keyword(title),
            "source": "zhihu",
            "rank": idx + 1,
        })

    return results


def _fetch_weibo_hotspots() -> list[dict[str, Any]]:
    """Scrape Weibo hot search for game/tech topics via public JSON API.

    Endpoint: https://weibo.com/ajax/side/hotSearch (no auth required).
    Returns up to 30 realtime hot search items, filtered for game relevance.
    """
    try:
        resp = httpx.get(
            "https://weibo.com/ajax/side/hotSearch",
            headers={
                "User-Agent": UA,
                "Referer": "https://weibo.com/",
                "Accept": "application/json",
            },
            timeout=15.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [WARN] Weibo hotspot scrape failed: {e}", file=sys.stderr)
        return []

    if not data.get("ok") or "data" not in data:
        return []

    realtime = data["data"].get("realtime", [])
    if not realtime:
        return []

    results: list[dict[str, Any]] = []
    for item in realtime[:30]:
        word = (item.get("word") or item.get("word_scheme") or "").strip()
        if not word or len(word) < 2:
            continue
        # Strip hashtag markers for cleaner keyword matching
        clean = word.strip("#")
        if not _is_game_relevant(clean):
            continue

        results.append({
            "keyword": _normalize_keyword(clean),
            "source": "weibo",
            "rank": item.get("realpos", len(results) + 1),
        })

    return results


def _fetch_xiaohongshu_hotspots() -> list[dict[str, Any]]:
    """Discover game-related trending topics on Xiaohongshu via search aggregation.

    XHS is a JS-rendered SPA with no public hot-search API. Instead of expensive
    Playwright scraping, we search "小红书 游戏 热门话题" through the existing
    engine chain and extract game-relevant keyword signals from result titles.

    This is a lightweight signal-discovery approach — it catches topics that are
    actively discussed on XHS without direct platform access. Upgrade to a direct
    XHS API if one becomes available.
    """
    queries = [
        "小红书 游戏 热门话题",
        "小红书 游戏 推荐 2026",
    ]
    all_keywords: list[dict[str, Any]] = []
    seen: set[str] = set()

    for query in queries:
        try:
            results = _search_with_fallback(query, max_results=5)
        except Exception as e:
            print(f"  [WARN] XHS search '{query}' failed: {e}", file=sys.stderr)
            continue

        for r in results:
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            combined = f"{title} {snippet}"

            if not _is_game_relevant(combined):
                continue

            # Extract likely game-related keyword from title
            kw = _normalize_keyword(title)
            if kw and len(kw) >= 2 and kw not in seen:
                seen.add(kw)
                all_keywords.append({
                    "keyword": kw,
                    "source": "xiaohongshu",
                    "rank": len(all_keywords) + 1,
                })

    return all_keywords


def _is_game_relevant(text: str) -> bool:
    """Check if a topic text is game/tech relevant."""
    # First, exclude clear non-game topics
    for signal in NON_GAME_SIGNALS:
        if signal in text:
            return False

    # Check for game/tech signals
    for signal in GAME_SIGNALS:
        if signal.lower() in text.lower():
            return True

    return False


def _normalize_keyword(raw: str) -> str:
    """Normalize a raw hot topic into a concise keyword phrase."""
    # Remove common prefixes/suffixes
    raw = re.sub(r"^(热|爆|沸|新)\s*", "", raw)
    # Truncate to ~20 chars
    if len(raw) > 20:
        # Try to cut at a natural boundary
        raw = raw[:20].rsplit("，", 1)[0].rsplit("。", 1)[0].rsplit(" ", 1)[0]
    return raw.strip()


# ═══════════════════════════════════════════════════════════════════
# Phase 1.5: Hot Topic Search
# ═══════════════════════════════════════════════════════════════════

# Engines are tried in order inside _search_with_fallback() — 360 → Sogou → Bing.
# DDG removed (html.duckduckgo.com returns 202 with no results even with VPN).


def search_hot_topics(date: str, force: bool = False) -> dict[str, Any]:
    """Search game-related news for each hot keyword → hot_topic_news table.

    360-first via domestic engines (no VPN dependency).
    Results are cached via search_cache (24h TTL).

    Returns:
        {"total_found": N, "keywords_searched": N, "warnings": [...]}
    """
    db = get_db()

    # ── Load keywords for this date ──
    keywords = db.get_hot_keywords_by_date(date)
    if not keywords:
        return {
            "total_found": 0,
            "keywords_searched": 0,
            "warnings": ["No hot keywords found for this date"],
        }

    warnings: list[str] = []

    # ── Search per keyword ──
    all_results: list[dict[str, Any]] = []
    keywords_searched = 0

    for kw in keywords:
        keyword = kw["keyword"]
        query = f"{keyword} 游戏行业"
        query_hash = hashlib.md5(f"{query}|{date}".encode()).hexdigest()

        # Check cache first (unless force). Skip empty cached results —
        # a transient failure that returned [] should not block re-search for 24h.
        if not force:
            try:
                cached = db.get_cached_search(query_hash, max_age_hours=24)
                if cached is not None and len(cached) > 0:
                    for r in cached:
                        r["keyword"] = keyword
                    all_results.extend(cached)
                    keywords_searched += 1
                    continue
            except Exception as e:
                print(f"  [WARN] search cache read failed for '{keyword}': {e}", file=sys.stderr)

        results = _search_with_fallback(query, max_results=5)
        keywords_searched += 1
        if results:
            for r in results:
                r["keyword"] = keyword
            all_results.extend(results)

            # Cache the results
            try:
                db.cache_search(
                    query_hash=query_hash,
                    query=query,
                    engine=results[0].get("search_engine", "unknown"),
                    results_json=json.dumps(results, ensure_ascii=False),
                    result_count=len(results),
                    called_by="hot_tracker",
                )
            except Exception as e:
                print(f"  [WARN] search cache write failed for '{query}': {e}", file=sys.stderr)

    # ── Dedup by URL ──
    seen_urls: set[str] = set()
    unique_results: list[dict[str, Any]] = []
    for r in all_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_results.append(r)

    # ── Write to DB ──
    if unique_results:
        try:
            from src.tools.url_utils import extract_domain

            records = [
                {
                    "keyword": r.get("keyword", ""),
                    "headline": r.get("title", ""),
                    "url": r.get("url", ""),
                    "source": extract_domain(r.get("url", "")),
                    "snippet": r.get("snippet", ""),
                    "search_engine": r.get("search_engine", ""),
                }
                for r in unique_results
            ]
            db.insert_hot_topic_news_deduped(records, date)

            # ── AI-powered selection + summarization ──
            # Replaces the old "first 7 by search order" with semantic judgment.
            # Falls back to simple first-7 on any Agent failure.
            try:
                ai_selected = _ai_filter_hot_topics(unique_results, date)
                if ai_selected:
                    ai_selected = _dedup_against_market_news(
                        ai_selected, date, unique_results
                    )
                    urls = [r["url"] for r in ai_selected if r.get("url")]
                    if urls:
                        db.mark_hot_topic_selected(urls, date)
                    # Persist AI summaries back to DB for render to use
                    _persist_ai_summaries(ai_selected, date, db)
            except Exception as e:
                print(f"  [WARN] Hot Tracker Agent failed, using fallback: {e}",
                      file=sys.stderr)
                # Fallback: simple first 7 by search order, deduped against market
                fallback_items = [
                    {
                        "headline": r.get("headline", ""),
                        "url": r.get("url", ""),
                        "source": r.get("source", ""),
                        "snippet": r.get("snippet", ""),
                        "search_engine": r.get("search_engine", ""),
                        "keyword": r.get("keyword", ""),
                    }
                    for r in records[:7]
                ]
                fallback_deduped = _dedup_against_market_news(
                    fallback_items, date, unique_results[:30]
                )
                urls = [r["url"] for r in fallback_deduped if r.get("url")]
                if urls:
                    db.mark_hot_topic_selected(urls, date)
        except Exception as e:
            warnings.append(f"Failed to write hot topic news to DB: {e}")

    return {
        "total_found": len(unique_results),
        "keywords_searched": keywords_searched,
        "warnings": warnings,
    }


def _dedup_against_market_news(
    selected: list[dict[str, Any]], date: str, pool: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Dedup hot topic items against today's market news headlines.

    If a hot topic headline shares dedup tokens with a market news headline,
    it's a duplicate — drop it and recruit the next available item from *pool*.

    Args:
        selected: Currently selected hot topic items (AI or fallback).
        date: Report date for loading market news.
        pool: Full unique_results pool (for recruiting replacements).

    Returns:
        Deduped list (may be shorter than input if pool is exhausted).
    """
    from src.storage.sqlite import get_db
    db = get_db()

    # Load today's market news headlines
    try:
        market_news = db.get_market_news_by_date(date)
    except Exception as e:
        print(f"  [WARN] Failed to load market news for dedup: {e}", file=sys.stderr)
        return selected  # can't dedup, return as-is

    if not market_news:
        return selected  # nothing to dedup against

    # Build token sets for all market news headlines
    market_token_sets: list[set[str]] = []
    for mn in market_news:
        hl = mn.get("headline", "") or ""
        tokens = headline_dedup_tokens(hl)
        if tokens:
            market_token_sets.append(tokens)

    if not market_token_sets:
        return selected

    # URLs of market news (for direct URL match)
    market_urls: set[str] = set()
    for mn in market_news:
        url = (mn.get("url", "") or "").lower().strip()
        if url:
            market_urls.add(url)

    deduped: list[dict[str, Any]] = []
    selected_urls: set[str] = set()

    for item in selected:
        item_url = (item.get("url", "") or "").lower().strip()

        # Direct URL match
        if item_url and item_url in market_urls:
            print(f"  [DEDUP] Hot topic URL matched market news: {item_url[:80]}",
                  file=sys.stderr)
            continue

        # Token-level match
        item_headline = item.get("headline", "") or item.get("title", "") or ""
        item_tokens = headline_dedup_tokens(item_headline)
        if item_tokens:
            is_dup = False
            for mt in market_token_sets:
                if item_tokens & mt:
                    overlap = item_tokens & mt
                    print(f"  [DEDUP] Hot topic tokens overlap with market: "
                          f"{item_headline[:60]}... ←→ {overlap}", file=sys.stderr)
                    is_dup = True
                    break
            if is_dup:
                continue

        deduped.append(item)
        selected_urls.add(item_url)

    # Recruit replacements from pool for dropped items
    dropped = len(selected) - len(deduped)
    if dropped > 0:
        print(f"  [DEDUP] {dropped} hot topic(s) dropped (market overlap),"
              f" recruiting replacements...", file=sys.stderr)
        for p in pool:
            p_url = (p.get("url", "") or "").lower().strip()
            # Skip already selected or dropped
            if p_url in selected_urls or p_url in market_urls:
                continue
            # Check token overlap
            p_headline = p.get("title", "") or p.get("headline", "") or ""
            p_tokens = headline_dedup_tokens(p_headline)
            if p_tokens:
                is_dup = False
                for mt in market_token_sets:
                    if p_tokens & mt:
                        is_dup = True
                        break
                if is_dup:
                    continue
            deduped.append({
                "headline": p_headline,
                "url": p_url,
                "source": p.get("source", "") or p.get("search_engine", ""),
                "snippet": p.get("snippet", ""),
                "search_engine": p.get("search_engine", ""),
                "keyword": p.get("keyword", ""),
            })
            selected_urls.add(p_url)
            if len(deduped) >= len(selected):
                break

    if len(deduped) < len(selected):
        print(f"  [DEDUP] Only {len(deduped)}/{len(selected)} items after dedup"
              f" (pool exhausted)", file=sys.stderr)

    return deduped


def _search_with_fallback(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search with 360-news-first fallback chain. Returns results with search_engine tag.

    DDG removed from chain (html.duckduckgo.com returns 202 with no results even
    with VPN — confirmed 2026-06-26). 360/Sogou handle Chinese game queries well.
    """
    engines: list[tuple[str, Any]] = [
        ("360-news", _scrape_360_news),
        ("sogou-news", _scrape_sogou_news),
    ]

    for engine_name, engine_fn in engines:
        try:
            result_str = engine_fn(query, max_results)
            result = json.loads(result_str)
            results = result.get("results", [])
            if results:
                for r in results:
                    r["search_engine"] = engine_name
                return results
        except Exception as e:
            print(f"  [WARN] search engine '{engine_name}' failed: {e}", file=sys.stderr)
            continue

    return []


# ═══════════════════════════════════════════════════════════════════

class _HotSelectedItem(BaseModel):
    """A single AI-selected hot topic item."""
    id: int                         # index into the candidates list
    ai_summary: str = ""            # 1-2 sentence Chinese summary
    value_score: int = 0            # 0-100 business value score


class _HotFilterOutput(BaseModel):
    """Validated output from the Hot Tracker Agent."""
    selected: list[_HotSelectedItem] = []
    discarded_count: int = 0


def _ai_filter_hot_topics(
    candidates: list[dict[str, Any]], date: str
) -> list[HotTopicItem]:
    """Run the Hot Tracker Agent to filter + summarise search results.

    The Agent gets a web_fetch tool so it can open URLs when the search
    snippet alone isn't enough to judge business value.  This is the
    system's only real Agent — it has a tool loop because the search
    results often have insufficient context for a single-call judgment.

    Returns up to 7 enriched items (original fields + ai_summary + value_score),
    or an empty list on total failure (caller falls back to simple first-7).
    """
    if not candidates:
        return []

    # ── Limit input to keep prompt size reasonable ──
    MAX_CANDIDATES = 30
    working = candidates[:MAX_CANDIDATES]

    # ── Build candidates JSON for the prompt ──
    candidates_for_prompt = []
    for i, c in enumerate(working):
        candidates_for_prompt.append({
            "id": i,
            "title": c.get("title", "") or c.get("headline", ""),
            "url": c.get("url", ""),
            "snippet": (c.get("snippet", "") or "")[:200],
            "keyword": c.get("keyword", ""),
        })
    candidates_json = json.dumps(candidates_for_prompt, ensure_ascii=False, indent=2)

    # ── web_fetch tool (wraps enrichment.fetch_article_body) ──
    def _web_fetch(url: str, **_kw: Any) -> str:
        """Fetch the body text of a web page. Use when a search snippet
        is too short to judge whether the article is valuable for
        game-industry decision-makers."""
        if not url:
            return json.dumps({"error": "empty URL"}, ensure_ascii=False)
        try:
            from src.agents.enrichment import fetch_article_body
            body = fetch_article_body(url, timeout=10)
            if not body:
                return json.dumps(
                    {"status": "empty", "hint": "page returned no readable text"},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"status": "ok", "text": body[:600]},
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    web_fetch_tool = Tool(
        name="web_fetch",
        description=(
            "Fetch the body text of a web page. Use this when a search "
            "result's snippet is too short to judge its business value. "
            "Only call this for candidates that look promising but need "
            "more context — do NOT fetch every candidate."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to fetch (from the candidate list)",
                },
            },
            "required": ["url"],
        },
        fn=_web_fetch,
    )

    # ── Run the Agent ──
    agent = Agent(
        "hot_tracker",
        tools=[web_fetch_tool],
        max_tool_rounds=4,
        max_tokens=8192,
        output_schema=_HotFilterOutput,
    )

    try:
        result = agent.run(
            date=date,
            total_candidates=len(working),
            keyword_count=len({c.get("keyword", "") for c in working}),
            candidates_json=candidates_json,
            _verbose=False,
        )
    except Exception as e:
        print(f"  [WARN] Hot Tracker Agent.run() failed: {e}", file=sys.stderr)
        return []

    selected = result.get("selected") or []
    if not selected:
        return []

    # ── Map agent output back to original candidate dicts ──
    enriched: list[dict[str, Any]] = []
    for item in selected:
        idx = item.get("id", -1) if isinstance(item, dict) else getattr(item, "id", -1)
        if idx < 0 or idx >= len(working):
            continue
        candidate = dict(working[idx])
        candidate["ai_summary"] = (
            item.get("ai_summary", "")
            if isinstance(item, dict)
            else getattr(item, "ai_summary", "")
        )
        candidate["value_score"] = (
            item.get("value_score", 0)
            if isinstance(item, dict)
            else getattr(item, "value_score", 0)
        )
        enriched.append(candidate)

    # Sort by value_score descending, return top 7
    enriched.sort(key=lambda x: x.get("value_score", 0), reverse=True)
    return enriched[:7]


def _persist_ai_summaries(
    selected: list[HotTopicItem], date: str, db: Any
) -> None:
    """Write AI summaries back to hot_topic_news rows matched by URL."""
    try:
        conn = db._connect()
        for item in selected:
            url = item.get("url", "")
            summary = item.get("ai_summary", "")
            score = item.get("value_score", 0)
            if url and summary:
                conn.execute(
                    """UPDATE hot_topic_news
                       SET ai_summary = ?, value_score = ?
                       WHERE url = ? AND date = ?""",
                    (summary, score, url, date),
                )
        conn.commit()
    except Exception as e:
        print(f"  [WARN] AI summary persistence failed: {e}", file=sys.stderr)
        # best-effort — summaries are non-critical
