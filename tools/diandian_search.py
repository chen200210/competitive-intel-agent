"""
点点数据按需搜索 — triggered by user clicking "🔍 查点点数据" on Feishu card.

Searches 点点数据 for a specific game, extracts download/revenue trends,
rank history, rating, and metadata. Results cached for 7 days.

Independent script — does NOT inherit ChartScraper. Reuses Playwright +
Chrome profile login state from diandian_batch.py.

Usage:
    python -m tools.diandian_search --game "暗夜防线"
    python -m tools.diandian_search --game "暗夜防线" --bundle "com.xxx.td"
    python -m tools.diandian_search --game "暗夜防线" --force  # skip cache

From code:
    from tools.diandian_search import search_game_on_diandian
    result = search_game_on_diandian("暗夜防线")
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Fix import path for running as script or module
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Playwright imported lazily

PROJECT_ROOT = _PROJECT_ROOT
CHROME_PROFILE = PROJECT_ROOT / "data" / ".diandian_chrome_profile"
BASE_URL = "https://app.diandian.com"
SEARCH_URL = f"{BASE_URL}/search"
CACHE_TTL_DAYS = 7


def _get_playwright():
    """Lazy import playwright."""
    from playwright.sync_api import sync_playwright
    return sync_playwright


# ── Public API ─────────────────────────────────────────────────


def search_game_on_diandian(
    game_name: str,
    bundle_id: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search for a game on 点点数据 and extract key metrics.

    This is the main entry point — called by feishu/bot.py when a user
    clicks the "🔍 查点点数据" button on a Feishu card.

    Args:
        game_name: Game display name to search for.
        bundle_id: Optional bundle ID for precise matching.
        force_refresh: If True, skip cache and re-search.

    Returns:
        {
            "found": bool,
            "game_name": str,
            "bundle_id": str | None,
            "downloads": {"last_30d": str, "trend": str} | None,
            "revenue": {"last_30d": str, "trend": str} | None,
            "rank_history": {"免费榜": [...], "畅销榜": [...]} | None,
            "rating": float | None,
            "developer": str,
            "genre": str,
            "theme": str,
            "source_url": str,
            "cached": bool,
            "error": str | None,
        }
    """
    # ── Check cache first ──
    if not force_refresh:
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            cached = db.get_diandian_search_cache(game_name, max_age_days=CACHE_TTL_DAYS)
            if cached:
                result = json.loads(cached.get("result_json", "{}"))
                if result.get("found"):
                    result["cached"] = True
                    return result
        except Exception:
            pass  # Cache miss → proceed to live search

    # ── Validate prerequisites ──
    if not CHROME_PROFILE.exists():
        return {
            "found": False,
            "game_name": game_name,
            "bundle_id": bundle_id,
            "error": "未找到登录态！请先运行 python tools/diandian_auth.py",
            "cached": False,
        }

    # ── Live search ──
    result = _do_search(game_name, bundle_id)

    # ── Cache result ──
    if result.get("found"):
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            db.cache_diandian_search(
                game_name=game_name,
                bundle_id=bundle_id or result.get("bundle_id", ""),
                search_date=datetime.now().strftime("%Y-%m-%d"),
                result_json=json.dumps(result, ensure_ascii=False),
            )
        except Exception:
            pass  # Cache write failure is non-fatal

    result["cached"] = False
    return result


# ── Core search logic ────────────────────────────────────────


def _do_search(
    game_name: str, bundle_id: str | None = None
) -> dict[str, Any]:
    """Execute the Playwright-based search and data extraction.

    Flow:
      1. Navigate to search page with keyword
      2. Parse search result list → find matching game
      3. Click into detail page
      4. Extract downloads, revenue, ranks, rating, metadata
    """
    base_result = {
        "found": False,
        "game_name": game_name,
        "bundle_id": bundle_id,
        "downloads": None,
        "revenue": None,
        "rank_history": None,
        "rating": None,
        "developer": "",
        "genre": "",
        "theme": "",
        "source_url": "",
        "error": None,
    }

    try:
        with _get_playwright()() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE),
                headless=False,
                viewport={"width": 1920, "height": 1080},
                args=["--disable-blink-features=AutomationControlled"],
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            """)
            page = context.new_page()

            # ── Step 1: Search ──
            search_url = f"{SEARCH_URL}?keyword={game_name}"
            print(f"  搜索: {search_url}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3_000)

            if "login" in page.url.lower():
                context.close()
                base_result["error"] = "未登录点点数据，请先运行 python tools/diandian_auth.py"
                return base_result

            # ── Step 2: Find and click matching game ──
            detail_url = _find_and_click_game(page, game_name, bundle_id)
            if not detail_url:
                # Try extracting data from search result list directly
                print("  ⚠️ 未找到匹配游戏详情页，尝试从搜索结果提取")
                data = _extract_from_search_results(page, game_name)
                if data:
                    data["source_url"] = search_url
                    data["found"] = True
                    context.close()
                    return {**base_result, **data}
                context.close()
                base_result["error"] = f"未在点点数据找到 '{game_name}'"
                return base_result

            base_result["source_url"] = detail_url
            page.wait_for_timeout(3_000)

            # ── Step 3: Extract detail page data ──
            data = _extract_detail_page(page, game_name)
            if data:
                base_result.update(data)
                base_result["found"] = True
            else:
                base_result["error"] = "详情页数据提取失败"

            context.close()

    except Exception as e:
        base_result["error"] = f"搜索过程异常: {e}"

    return base_result


# ── Page interaction ─────────────────────────────────────────


def _find_and_click_game(page, game_name: str, bundle_id: str | None = None) -> str | None:
    """Find the best-matching game in search results and click into it.

    Returns the detail page URL if successful, None otherwise.
    """
    # Extract all clickable game links from search results
    candidates: list[dict[str, Any]] = page.evaluate("""
        (targetName) => {
            const results = [];
            // Find all links that look like game entries
            const allLinks = document.querySelectorAll('a[href*="/app/"]');
            for (const link of allLinks) {
                const text = link.textContent.trim();
                const href = link.getAttribute('href') || '';
                // Collect parent/sibling text for name matching
                const parent = link.closest('[class*="item"], [class*="row"], [class*="card"], li, div');
                const context = parent ? parent.textContent.trim().slice(0, 200) : text;
                if (text.length >= 2 && text.length <= 80) {
                    results.push({
                        text: text,
                        href: href,
                        context: context,
                        similarity: 0,
                    });
                }
            }
            // Simple similarity: check if target name chars appear in order
            for (const r of results) {
                let ti = 0;
                const ctx = r.context.toLowerCase();
                const tgt = targetName.toLowerCase();
                for (let ci = 0; ci < ctx.length && ti < tgt.length; ci++) {
                    if (ctx[ci] === tgt[ti]) ti++;
                }
                r.similarity = ti / tgt.length;
            }
            results.sort((a, b) => b.similarity - a.similarity);
            return results.slice(0, 10);
        }
    """, game_name)

    if not candidates:
        return None

    best = candidates[0]
    similarity = best.get("similarity", 0)
    print(f"  最佳匹配: '{best.get('text', '?')}' (相似度 {similarity:.0%})")

    if similarity < 0.4:
        print(f"  ⚠️ 相似度过低，可能不是同一个游戏")
        return None

    # Click the best match
    try:
        href = best["href"]
        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
        # Try clicking the link
        selector = f'a[href="{href}"]'
        try:
            page.locator(selector).first.click(timeout=5_000)
        except Exception:
            # Fallback: navigate directly
            page.goto(full_url, wait_until="domcontentloaded", timeout=30_000)
        return full_url
    except Exception as e:
        print(f"  [WARN] 点击游戏失败: {e}")
        return None


def _extract_from_search_results(page, game_name: str) -> dict[str, Any] | None:
    """Fallback: extract whatever data is visible in search results list.

    Used when we can't navigate to a detail page.
    """
    try:
        data: dict[str, Any] = page.evaluate("""
            () => {
                const result = {developer: '', genre: '', rating: null, downloads: null};
                // Try to find game card elements
                const cards = document.querySelectorAll('[class*="card"], [class*="item"], [class*="row"]');
                for (const card of cards) {
                    const text = card.textContent;
                    // Rating
                    const ratingMatch = text.match(/([\\d.]+)\\s*分/);
                    if (ratingMatch && !result.rating) {
                        result.rating = parseFloat(ratingMatch[1]);
                    }
                    // Downloads
                    const dlMatch = text.match(/([\\d.]+[万千百万])\\s*(下载|安装|关注)/);
                    if (dlMatch && !result.downloads) {
                        result.downloads = {last_30d: dlMatch[0], trend: '未知'};
                    }
                    // Developer
                    const devMatch = text.match(/(开发商|开发者|发行商|厂商)[：:\\s]*([^\\s]{2,20})/);
                    if (devMatch && !result.developer) {
                        result.developer = devMatch[2];
                    }
                }
                return result;
            }
        """)
        return data if any(v for v in data.values() if v) else None
    except Exception:
        return None


def _extract_detail_page(page, game_name: str) -> dict[str, Any] | None:
    """Extract game metrics from the detail page.

    Uses a combination of DOM queries and full-text analysis.
    """
    try:
        page.wait_for_timeout(2_000)

        data: dict[str, Any] = page.evaluate("""
            () => {
                const result = {
                    downloads: null,
                    revenue: null,
                    rank_history: null,
                    rating: null,
                    developer: '',
                    genre: '',
                    theme: '',
                    bundle_id: null,
                };

                // Get all visible text for pattern matching
                const body = document.body.innerText;

                // ── Rating ──
                const ratingMatch = body.match(/([\\d.]+)\\s*分/);
                if (ratingMatch) result.rating = parseFloat(ratingMatch[1]);

                // ── Developer ──
                const devMatch = body.match(/(?:开发商|开发者|发行商|厂商)[：:\\s]*([^\\n]{2,30})/);
                if (devMatch) result.developer = devMatch[1].trim();

                // ── Genre ──
                const genreMatch = body.match(/(?:品类|分类|类型|游戏类型)[：:\\s]*([^\\n]{2,20})/);
                if (genreMatch) result.genre = genreMatch[1].trim();

                // ── Downloads ──
                const dlPatterns = [
                    /(?:近30天|最近30天|30天)\\s*(?:下载|安装)[：:\\s]*([^\\n]{2,20})/,
                    /(?:下载量|总下载)[：:\\s]*([^\\n]{2,20})/,
                    /([\\d.]+[万千百万])\\s*(?:下载|安装)/,
                ];
                for (const pat of dlPatterns) {
                    const m = body.match(pat);
                    if (m) {
                        result.downloads = {last_30d: m[1].trim(), trend: '未知'};
                        break;
                    }
                }

                // ── Revenue ──
                const revPatterns = [
                    /(?:近30天|最近30天|30天)\\s*(?:收入|营收)[：:\\s]*([^\\n]{2,20})/,
                    /(?:收入|营收)[：:\\s]*([^\\n]{2,20})/,
                    /\\$[\\d.,]+[万千百万]/,
                ];
                for (const pat of revPatterns) {
                    const m = body.match(pat);
                    if (m) {
                        result.revenue = {last_30d: m[1] ? m[1].trim() : m[0], trend: '未知'};
                        break;
                    }
                }

                // ── Trend indicators ──
                if (result.downloads) {
                    if (/上升|增长|上涨|↑/.test(body.slice(0, 500))) {
                        result.downloads.trend = '上升';
                    } else if (/下降|下滑|下跌|↓/.test(body.slice(0, 500))) {
                        result.downloads.trend = '下降';
                    } else {
                        result.downloads.trend = '稳定';
                    }
                }

                // ── Theme ──
                const themeKeywords = ['科幻', '奇幻', '武侠', '仙侠',
                    '末日', '丧尸', '战争', '历史', '神话', '二次元', '像素', '赛博朋克'];
                for (const kw of themeKeywords) {
                    if (body.includes(kw)) { result.theme = kw; break; }
                }

                // ── Bundle ID ──
                const bidMatch = body.match(/(?:bundle\\s*id|包名|BundleID)[：:\\s]*([a-zA-Z][\\w.]+)/i);
                if (bidMatch) result.bundle_id = bidMatch[1];

                return result;
            }
        """)

        # ── Try to extract rank history from charts/tables ──
        try:
            rank_data = _extract_rank_history(page)
            if rank_data:
                data["rank_history"] = rank_data
        except Exception:
            pass

        return data if any(v for v in data.values() if v) else None

    except Exception as e:
        print(f"  [WARN] 详情页提取失败: {e}")
        return None


def _extract_rank_history(page) -> dict[str, list[dict[str, Any]]] | None:
    """Try to extract ranking history from chart elements on the page."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        rank_data: dict[str, list[dict[str, Any]]] = page.evaluate("""
            (dates) => {
                const result = {};
                const body = document.body.innerText;

                // Look for rank numbers near chart type labels
                const patterns = [
                    {label: '免费榜', re: /免费榜[^\\d]*(\\d+)/},
                    {label: '畅销榜', re: /畅销榜[^\\d]*(\\d+)/},
                    {label: '热门榜', re: /热门榜[^\\d]*(\\d+)/},
                    {label: '下载榜', re: /下载榜[^\\d]*(\\d+)/},
                    {label: '收入榜', re: /收入榜[^\\d]*(\\d+)/},
                ];

                for (const {label, re} of patterns) {
                    const match = body.match(re);
                    if (match) {
                        result[label] = [
                            {date: dates.today, rank: parseInt(match[1])},
                        ];
                    }
                }

                return Object.keys(result).length > 0 ? result : null;
            }
        """, {"today": today, "yesterday": yesterday})

        return rank_data
    except Exception:
        return None


# ── CLI ─────────────────────────────────────────────────────


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser(description="点点数据按需搜索")
    parser.add_argument("--game", type=str, required=True, help="游戏名称")
    parser.add_argument("--bundle", type=str, default=None, help="Bundle ID (可选)")
    parser.add_argument("--force", action="store_true", help="跳过缓存，强制重新搜索")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 结果")
    args = parser.parse_args()

    if not args.json:
        print("=" * 50)
        print(f"🔍 点点数据搜索: {args.game}")
        if args.bundle:
            print(f"   Bundle ID: {args.bundle}")
        if args.force:
            print("   ⚡ 强制刷新（跳过缓存）")
        print("=" * 50)

    result = search_game_on_diandian(
        game_name=args.game,
        bundle_id=args.bundle,
        force_refresh=args.force,
    )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
