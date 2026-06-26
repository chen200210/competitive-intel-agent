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
import sys
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


# ── 360 Search (primary for Chinese queries) ──────────────────

def _scrape_360(query: str, max_results: int = 5) -> str:
    """Scrape 360 Search (so.com) — excellent Chinese game search results.

    Unlike Sogou/Bing, 360 actually returns game-related pages for
    Chinese game queries, not dictionary definitions.
    """
    resp = httpx.get(
        "https://www.so.com/s",
        params={"q": query},
        headers={
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        timeout=15.0,
        follow_redirects=True,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []

    for item in soup.select(".res-list, .result")[:max_results]:
        title_el = item.select_one("h3 a") or item.select_one("a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")

        snippet_el = item.select_one(".res-desc, .res-summary, p")
        snippet = snippet_el.get_text(strip=True)[:300] if snippet_el else ""

        if title:
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet,
            })

    if not results:
        return json.dumps(
            {"query": query, "results": [], "engine": "360",
             "note": "No results found."},
            ensure_ascii=False,
        )

    return json.dumps({"query": query, "results": results, "engine": "360"}, ensure_ascii=False)


# ── Sogou (fallback) ───────────────────────────────────────────

def _scrape_sogou(query: str, max_results: int = 5) -> str:
    """Scrape Sogou search results — excellent Chinese + English game search.

    Accessible from China without proxy. Includes a small delay to avoid
    rate-limiting during parallel agent calls.
    """
    import time
    time.sleep(0.3)  # anti-rate-limit: 300ms between requests

    resp = httpx.get(
        "https://www.sogou.com/web",
        params={"query": query},
        headers={
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        timeout=15.0,
        follow_redirects=True,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []

    # Detect captcha / verification page
    if "验证码" in resp.text or "异常请求" in resp.text:
        return json.dumps(
            {"query": query, "results": [], "engine": "sogou",
             "note": "Sogou captcha triggered — will retry on next call."},
            ensure_ascii=False,
        )

    for item in soup.select("div.results > div")[:max_results]:
        title_el = item.select_one("h3 a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")

        # Sogou uses redirect URLs — keep as-is for web_fetch to follow
        if href.startswith("/"):
            href = f"https://www.sogou.com{href}"

        snippet_el = item.select_one(".str-text, .space-txt, .abstract, p")
        snippet = snippet_el.get_text(strip=True)[:300] if snippet_el else ""

        if title:
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet,
            })

    if not results:
        return json.dumps(
            {"query": query, "results": [], "engine": "sogou",
             "note": "No results found. Sogou may have changed their HTML structure."},
            ensure_ascii=False,
        )

    return json.dumps({"query": query, "results": results, "engine": "sogou"}, ensure_ascii=False)



# ── 360 News Search ────────────────────────────────────────────

def _scrape_360_news(query: str, max_results: int = 5) -> str:
    """Scrape 360 News Search (news.so.com) — returns recent news, not SEO pages.

    Unlike web search which returns stale listicles, news.so.com returns
    results from hours/days ago with timestamps like "4小时前" / "1天前".
    """
    resp = httpx.get(
        "https://news.so.com/ns",
        params={"q": query, "src": "srp"},
        headers={
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        timeout=15.0,
        follow_redirects=True,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []

    # 360 news results are in <li> elements
    for li in soup.select("li"):
        a = li.select_one("h3 a") or li.select_one("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        if len(title) < 10:
            continue
        href = a.get("href", "")

        # Extract time (e.g. "4小时前", "1天前", "2026-05-28")
        time_str = ""
        for sel in ["[class*='time']", ".date", ".source", "span:last-child"]:
            time_el = li.select_one(sel)
            if time_el:
                time_str = time_el.get_text(strip=True)
                break

        # Extract snippet from any paragraph
        snippet = ""
        for sel in ["p", ".desc", ".summary"]:
            p_el = li.select_one(sel)
            if p_el:
                snippet = p_el.get_text(strip=True)[:300]
                break

        # Combine title with time for context
        if time_str and time_str not in title:
            title = f"{title} ({time_str})"

        results.append({
            "title": title,
            "url": href,
            "snippet": snippet,
        })

        if len(results) >= max_results:
            break

    if not results:
        return json.dumps(
            {"query": query, "results": [], "engine": "360-news",
             "note": "No news results found."},
            ensure_ascii=False,
        )

    return json.dumps({"query": query, "results": results, "engine": "360-news"}, ensure_ascii=False)


# ── Sogou News Search ──────────────────────────────────────────

def _scrape_sogou_news(query: str, max_results: int = 5) -> str:
    """Scrape Sogou News Search (news.sogou.com) — time-sorted news results.

    Uses sort=1 (by time) for freshest results first.
    Includes a small delay to avoid rate-limiting.
    """
    import time
    time.sleep(0.5)  # anti-rate-limit

    resp = httpx.get(
        "https://news.sogou.com/news",
        params={"query": query, "sort": 1},
        headers={
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        timeout=15.0,
        follow_redirects=True,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []

    # Sogou news results: div.results > div.vrwrap > div.news200616
    for item in soup.select("div.results div.vrwrap")[:max_results]:
        title_el = item.select_one("h3.vr-title a") or item.select_one("h3 a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        if href.startswith("/"):
            href = f"https://news.sogou.com{href}"

        # Get full text for snippet (includes source + date + summary)
        text = item.get_text(strip=True)
        # Remove the title from text to avoid duplication
        if text.startswith(title):
            text = text[len(title):].strip()
        snippet = text[:300]

        if title:
            results.append({
                "title": title,
                "url": href,
                "snippet": snippet,
            })

    if not results:
        return json.dumps(
            {"query": query, "results": [], "engine": "sogou-news",
             "note": "No news results found."},
            ensure_ascii=False,
        )

    return json.dumps({"query": query, "results": results, "engine": "sogou-news"}, ensure_ascii=False)
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
    except Exception as e:
        print(f"  [WARN] search cache lookup failed: {e}", file=sys.stderr)
        pass

    # ── Real search: try engines in order ──
    engines = [
        ("360", _scrape_360),
        ("sogou", _scrape_sogou),
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
    except Exception as e:
        print(f"  [WARN] search cache write failed: {e}", file=sys.stderr)
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
