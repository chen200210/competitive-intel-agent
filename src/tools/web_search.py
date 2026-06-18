"""
Web search tool — Tavily Search API (primary) with Bing/DDG fallback.

Tavily returns extracted page content alongside search results,
so the LLM often doesn't need a separate web_fetch call.

Free tier: 1000 searches/month. Sign up at https://tavily.com

Usage:
    from src.tools.web_search import web_search
    results = web_search("今日游戏行业新闻", max_results=5)
"""

from __future__ import annotations

import hashlib
import json
from datetime import date as _date
from typing import Any

import httpx
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ── Tavily (primary) ───────────────────────────────────────────

def _search_tavily(query: str, max_results: int = 5) -> str:
    """Search via Tavily API — returns results with extracted page content."""
    from src.config import settings

    if not settings.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY not set")

    from tavily import TavilyClient
    client = TavilyClient(api_key=settings.tavily_api_key)

    response = client.search(
        query,
        max_results=max_results,
        include_raw_content=False,
        include_domains=[],  # let Tavily decide
    )

    results: list[dict[str, Any]] = []
    total_chars = 0
    char_budget = 6000  # keep total under limit to avoid truncation issues

    for r in response.get("results", []):
        content = r.get("content", "")
        # Truncate per-result content to stay within budget
        content_limit = max(200, (char_budget - total_chars) // max(1, len(response.get("results", [])) - len(results)))
        if len(content) > content_limit:
            content = content[:content_limit] + "..."
        total_chars += len(content)

        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": content[:300],
            "content": content,
            "score": r.get("score", 0.0),
        })

    if not results:
        return json.dumps(
            {"query": query, "results": [], "engine": "tavily",
             "note": "Tavily returned no results."},
            ensure_ascii=False,
        )

    result_str = json.dumps(
        {"query": query, "results": results, "engine": "tavily"},
        ensure_ascii=False,
    )
    return result_str


# ── Bing (fallback) ────────────────────────────────────────────

def _scrape_bing(query: str, max_results: int = 5) -> str:
    """Scrape Bing search results (free, accessible from China)."""
    resp = httpx.get(
        "https://www.bing.com/search",
        params={"q": query, "count": max_results},
        headers={"User-Agent": UA},
        timeout=15.0,
        follow_redirects=True,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []

    for item in soup.select("li.b_algo")[:max_results]:
        title_link = item.select_one("h2 a")
        if not title_link:
            continue

        title = title_link.get_text(strip=True)
        url = title_link.get("href", "")

        snippet_el = item.select_one(".b_caption p") or item.select_one(".b_caption")
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""

        cite_el = item.select_one("cite")
        display_url = cite_el.get_text(strip=True) if cite_el else ""

        if title:
            results.append({
                "title": title,
                "url": url or display_url,
                "snippet": snippet,
            })

    if not results:
        return json.dumps(
            {"query": query, "results": [], "engine": "bing",
             "note": "No results found. Bing may have changed their HTML structure."},
            ensure_ascii=False,
        )

    return json.dumps({"query": query, "results": results, "engine": "bing"}, ensure_ascii=False)


def _scrape_ddg(query: str, max_results: int = 5) -> str:
    """Scrape DuckDuckGo HTML search (last-resort fallback)."""
    resp = httpx.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": UA},
        timeout=15.0,
        follow_redirects=True,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []

    for item in soup.select(".result, .web-result, .result__body")[:max_results]:
        title_el = item.select_one(".result__title, .result__a, a.result__a")
        snippet_el = item.select_one(".result__snippet, .snippet")
        link_el = item.select_one(".result__url")

        title = title_el.get_text(strip=True) if title_el else ""
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        link = link_el.get("href", "") if link_el else ""

        if title:
            results.append({"title": title, "url": link, "snippet": snippet})

    if not results:
        return json.dumps(
            {"query": query, "results": [], "engine": "ddg",
             "note": "No results found."},
            ensure_ascii=False,
        )

    return json.dumps({"query": query, "results": results, "engine": "ddg"}, ensure_ascii=False)


# ── Main entry ─────────────────────────────────────────────────

def web_search(query: str, max_results: int = 5, **_meta: Any) -> str:
    """Search the web — Tavily API → Bing → DDG. Cached per query+date.

    Args:
        query: Search query string.
        max_results: Max number of results to return (default 5, max 10).
        _meta: Internal kwargs injected by Agent (_called_by, _run_id, _target_date).

    Returns:
        JSON string with {query, results, engine, cache_hit}.
        Tavily results include `content` (extracted page text) per result.
    """
    today = _date.today().isoformat()
    query_hash = hashlib.md5(f"{query}|{today}".encode()).hexdigest()
    called_by = _meta.get("_called_by", "unknown") if _meta else "unknown"

    # ── Check cache ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        cached = db.get_cached_search(query_hash, max_age_hours=24)
        if cached is not None:
            # If Tavily is available and the cached result isn't from Tavily,
            # bypass cache to get richer results (content extraction).
            from src.config import settings
            if settings.tavily_api_key:
                cached_sample = cached[0] if cached else {}
                if "content" not in cached_sample:
                    pass  # stale non-Tavily cache → re-search with Tavily
                else:
                    return json.dumps(
                        {"query": query, "results": cached, "engine": "cache", "cache_hit": True},
                        ensure_ascii=False,
                    )
            else:
                return json.dumps(
                    {"query": query, "results": cached, "engine": "cache", "cache_hit": True},
                    ensure_ascii=False,
                )
    except Exception:
        pass

    # ── Real search: try engines in order ──
    engines = [
        ("tavily", _search_tavily),
        ("bing", _scrape_bing),
        ("ddg", _scrape_ddg),
    ]

    result_str = ""
    used_engine = "unknown"
    last_error = ""

    for engine_name, engine_fn in engines:
        try:
            result_str = engine_fn(query, max_results)
            used_engine = engine_name
            break
        except Exception as e:
            last_error = _shorten(str(e))
            continue
    else:
        # All engines failed
        result_str = json.dumps(
            {
                "query": query,
                "error": f"All search engines failed. Last error: {last_error}",
                "results": [],
            },
            ensure_ascii=False,
        )

    # Add cache_hit flag
    parsed = json.loads(result_str)
    parsed["cache_hit"] = False

    # ── Write cache ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        results_json = json.dumps(parsed.get("results", []), ensure_ascii=False)
        db.cache_search(
            query_hash=query_hash,
            query=query,
            engine=used_engine,
            results_json=results_json,
            result_count=len(parsed.get("results", [])),
            called_by=called_by,
        )
    except Exception:
        pass

    return json.dumps(parsed, ensure_ascii=False)


def _shorten(s: str, max_len: int = 120) -> str:
    """Truncate error messages for JSON output."""
    return s if len(s) <= max_len else s[:max_len - 3] + "..."


# ── Tool descriptor for Agent registration ────────────────────

TOOL_DESCRIPTOR: dict[str, Any] = {
    "name": "web_search",
    "description": (
        "Search the web for information. "
        "Use this to find news, events, and data about games and the gaming industry. "
        "Returns a list of search results with title, URL, snippet, and often "
        "extracted page content (so you may not need to call web_fetch separately)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string, e.g. '2026年6月游戏行业新闻'",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (default 5, max 10)",
            },
        },
        "required": ["query"],
    },
}
