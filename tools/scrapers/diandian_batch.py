"""
点点数据 — 多榜单批量抓取。

支持 iOS + Android 平台的全部榜单类型（免费榜/畅销榜/热门榜/下载榜/收入榜）。
基于 ChartScraper 基类，输出标准 CSV 到 data/raw/。

用法:
    # 1. 发现模式：打开页面，提取所有可用榜单的 URL
    python tools/scrapers/diandian_batch.py --discover

    # 2. 批量抓取所有已配置的榜单
    python tools/scrapers/diandian_batch.py --all

    # 3. 只抓取指定榜单
    python tools/scrapers/diandian_batch.py --charts ios_free,ios_grossing,android_hot

    # 4. 只抓取 iOS 平台的全部榜单
    python tools/scrapers/diandian_batch.py --platform ios

前置条件:
    python tools/diandian_auth.py   # 首次运行一次，保存登录态
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# Playwright is imported lazily — only when actually scraping

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHROME_PROFILE = PROJECT_ROOT / "data" / ".diandian_chrome_profile"
OUTPUT_DIR = PROJECT_ROOT / "data" / "raw"
DISCOVERY_CACHE = PROJECT_ROOT / "data" / ".diandian_charts.json"


def _get_playwright():
    """Lazy import playwright (only needed for scraping, not --list)."""
    from playwright.sync_api import sync_playwright
    return sync_playwright

# ---- 抓取参数 ----
MAX_ROWS = 200
SCROLL_PX = 400
SCROLL_PAUSE = 1.2
POPUP_INTERVAL = 10

# ---- 榜单 URL 配置（已知的） ----
# 格式: key → {platform, chart_type, url_path}
# url_path 不含域名，如 "/rank/ios/1-4-0-75-2"
KNOWN_CHARTS: dict[str, dict[str, str]] = {
    # ── iOS ──
    "ios_free": {
        "platform": "iOS",
        "chart_type": "免费榜",
        "url_path": "/rank/ios/1-4-0-75-2",
    },
    "ios_grossing": {
        "platform": "iOS",
        "chart_type": "畅销榜",
        "url_path": "/rank/ios/1-4-0-75-3",
    },
    "ios_hot": {
        "platform": "iOS",
        "chart_type": "热门榜",
        "url_path": "/rank/ios/1-4-0-75-4",
    },
    "ios_download": {
        "platform": "iOS",
        "chart_type": "下载榜",
        "url_path": "/rank/ios/1-4-0-75-5",
    },
    "ios_revenue": {
        "platform": "iOS",
        "chart_type": "收入榜",
        "url_path": "/rank/ios/1-4-0-75-6",
    },
    # ── Android ──
    "android_free": {
        "platform": "Android",
        "chart_type": "免费榜",
        "url_path": "/rank/android/1-4-0-75-2",
    },
    "android_grossing": {
        "platform": "Android",
        "chart_type": "畅销榜",
        "url_path": "/rank/android/1-4-0-75-3",
    },
    "android_hot": {
        "platform": "Android",
        "chart_type": "热门榜",
        "url_path": "/rank/android/1-4-0-75-4",
    },
    "android_download": {
        "platform": "Android",
        "chart_type": "下载榜",
        "url_path": "/rank/android/1-4-0-75-5",
    },
    "android_revenue": {
        "platform": "Android",
        "chart_type": "收入榜",
        "url_path": "/rank/android/1-4-0-75-6",
    },
}

BASE_URL = "https://app.diandian.com"


# ==================================================================
# 发现模式：从页面 DOM 提取所有可用榜单
# ==================================================================

def discover() -> int:
    """
    Open the rank page and extract all available chart tabs/links.
    Saves discovered charts to DISCOVERY_CACHE for later use.
    """
    print("=" * 60)
    print("🔍 点点数据 - 榜单发现模式")
    print(f"   Chrome 配置: {CHROME_PROFILE}")
    print("=" * 60)

    if not CHROME_PROFILE.exists():
        print("\n❌ 未找到登录态！请先运行:")
        print("   python tools/diandian_auth.py")
        return 1

    discovered: dict[str, dict[str, str]] = {}

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

            # 从 iOS 游戏免费榜开始
            page.goto(f"{BASE_URL}/rank/ios/1-4-0-75-2", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(5_000)

            if "login" in page.url.lower():
                print("❌ 未登录！请先运行: python tools/diandian_auth.py")
                context.close()
                return 1

            # ---- 1. 提取平台切换按钮 ----
            print("\n[1] 平台切换选项:")
            platforms_raw = page.evaluate("""
                () => {
                    const results = [];
                    // Look for platform tabs — common selectors
                    for (const sel of ['[class*="platform"]', '[class*="tab"]', 'button', 'a', 'span', 'div']) {
                        for (const el of document.querySelectorAll(sel)) {
                            const text = el.textContent.trim();
                            if (['iOS', 'Android', 'android', 'ios'].includes(text) && text.length <= 10) {
                                const href = el.getAttribute('href') || el.closest('a')?.getAttribute('href') || '';
                                const data = el.getAttribute('data-value') || el.getAttribute('data-key') || '';
                                results.push({text, href, data, tag: el.tagName, class: el.className});
                            }
                        }
                    }
                    return results.slice(0, 20);
                }
            """)
            for p in platforms_raw:
                print(f"  {p}")

            # ---- 2. 提取榜单类型切换 ----
            print("\n[2] 榜单类型切换选项:")
            chart_tabs = page.evaluate("""
                () => {
                    const results = [];
                    const keywords = ['免费榜', '畅销榜', '热门榜', '下载榜', '收入榜', '免费', '畅销', '热门', '下载', '收入'];
                    for (const el of document.querySelectorAll('a, button, span, div[class*="tab"], li')) {
                        const text = el.textContent.trim();
                        for (const kw of keywords) {
                            if (text === kw || text.startsWith(kw)) {
                                const href = el.getAttribute('href') || el.closest('a')?.getAttribute('href') || '';
                                const onclick = el.getAttribute('onclick') || '';
                                results.push({text, href, onclick, tag: el.tagName, class: el.className.slice(0, 60)});
                                break;
                            }
                        }
                    }
                    return results.slice(0, 30);
                }
            """)
            for ct in chart_tabs:
                print(f"  {ct}")

            # ---- 3. 提取当前 URL 中可变的参数 ----
            print("\n[3] 当前页面 URL:")
            current_url = page.url
            print(f"  {current_url}")

            # ---- 4. 尝试点击切换平台看 URL 变化 ----
            print("\n[4] 尝试切换平台...")
            for platform_label in ["Android", "android"]:
                try:
                    elem = page.locator(f"text={platform_label}").first
                    if elem.is_visible(timeout=2_000):
                        elem.click()
                        page.wait_for_timeout(3_000)
                        new_url = page.url
                        print(f"  点击 '{platform_label}' → URL: {new_url}")
                        break
                except Exception:
                    continue

            # ---- 5. 尝试点击切换榜单类型看 URL 变化 ----
            print("\n[5] 尝试切换榜单类型（依次点击各 tab，记录 URL 变化）...")
            for chart_label in ["畅销榜", "热门榜", "下载榜", "收入榜"]:
                try:
                    elem = page.locator(f"text={chart_label}").first
                    if elem.is_visible(timeout=2_000):
                        elem.click()
                        page.wait_for_timeout(3_000)
                        new_url = page.url
                        discovered[chart_label] = {"url": new_url}
                        print(f"  点击 '{chart_label}' → URL: {new_url}")
                except Exception as e:
                    print(f"  ❌ 点击 '{chart_label}' 失败: {e}")

            context.close()

    except Exception as e:
        print(f"\n❌ 发现过程出错: {e}")
        traceback.print_exc()
        return 1

    # ---- 保存发现结果 ----
    print(f"\n{'='*60}")
    print(f"📋 发现结果")
    print(f"{'='*60}")
    print(f"已知榜单: {len(KNOWN_CHARTS)} 个")
    print(f"新发现:   {len(discovered)} 个")

    # Merge with known
    merged = dict(KNOWN_CHARTS)
    for label, info in discovered.items():
        key = label.replace("榜", "").lower()
        merged[f"discovered_{key}"] = {
            "platform": "unknown",
            "chart_type": label,
            "url_path": info["url"].replace(BASE_URL, ""),
        }

    DISCOVERY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(DISCOVERY_CACHE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"已保存到: {DISCOVERY_CACHE}")
    print("\n✅ 发现完成! 请检查上方的 URL 变化，更新 KNOWN_CHARTS 配置。")
    return 0


# ==================================================================
# 抓取逻辑（从 diandian_scroll.py 复用）
# ==================================================================

def _switch_to_game_category(page) -> None:
    """切换到「游戏」分类标签."""
    for sel in ["text=游戏", "span:has-text('游戏')", "[class*='tab']:has-text('游戏')"]:
        try:
            elem = page.locator(sel).first
            if elem.is_visible(timeout=2_000) and elem.inner_text().strip() == "游戏":
                elem.click()
                page.wait_for_timeout(3_000)
                print("  ✓ 已切换到「游戏」")
                return
        except Exception:
            continue


def _collect_visible(page, collected: dict[int, list[str]]) -> None:
    """Extract visible ranking rows from the current viewport."""
    rows_data: list[list[str]] = page.evaluate("""
        () => {
            const results = [];
            const rowElements = document.querySelectorAll('[class*="row"]');
            for (const el of rowElements) {
                if (el.offsetHeight === 0) continue;
                const texts = [];
                const walk = (node) => {
                    if (node.nodeType === 3) {
                        const t = node.textContent.trim();
                        if (t && t.length < 80) texts.push(t);
                    } else if (node.nodeType === 1) {
                        const tag = node.tagName.toLowerCase();
                        if (['span','div','p','td','th','a','li'].includes(tag) && node.children.length === 0) {
                            const t = node.textContent.trim();
                            if (t && t.length < 80) texts.push(t);
                        } else {
                            for (const child of node.children) walk(child);
                        }
                    }
                };
                walk(el);
                if (texts.length >= 2) results.push(texts);
            }
            return results;
        }
    """)

    for values in rows_data:
        first = values[0].strip() if values else ""
        if not first.isdigit():
            continue
        rank = int(first)
        if rank < 1 or rank > 500:
            continue
        if rank not in collected:
            collected[rank] = values


def _clean_rows(collected: dict[int, list[str]], platform: str, chart_type: str, scrape_date: str) -> list[dict[str, str]]:
    """Clean collected rows into standard CSV format matching Loader's COLUMN_ALIASES.

    Column layouts per chart type (from DOM diagnostics):
      免费榜/热门榜 (14 cols):
        [0]=rank [1]=badge [2]=game_name [3]=developer
        [4-8]=internal ranks [9]=genre [10]=metric_a [11]=rating [12]=metric_b [13]=date
      畅销榜 (15 cols):
        [0]=rank [1]=badge [2]=game_name [3]=price [4]=developer
        [5-9]=internal ranks [10]=genre [11]=metric_a [12]=rating [13]=metric_b [14]=date
    """
    if not collected:
        return []

    col_counts = Counter(len(v) for v in collected.values())
    best_cols = col_counts.most_common(1)[0][0]

    # Per-chart-type column mapping
    if chart_type in ("畅销榜",):
        COL_NAMES = [
            "rank", "badge", "game_name", "price", "developer",
            "unk1", "scope", "unk2", "category_label", "unk3",
            "genre", "metric_a", "rating", "metric_b", "update_date",
        ]
    else:  # 免费榜, 热门榜, 下载榜, 收入榜
        COL_NAMES = [
            "rank", "badge", "game_name", "developer",
            "unk1", "scope", "unk2", "category_label", "unk3",
            "genre", "metric_a", "rating", "metric_b", "update_date",
        ]

    # Only keep columns needed for import + key metrics
    KEEP_COLS = {"rank", "game_name", "developer", "genre", "price", "rating"}

    result: list[dict[str, str]] = []
    for rank in sorted(collected.keys()):
        values = collected[rank]
        if len(values) < best_cols:
            values = values + [""] * (best_cols - len(values))
        values = values[:best_cols]

        row: dict[str, str] = {}
        for i, v in enumerate(values):
            name = COL_NAMES[i] if i < len(COL_NAMES) else f"col_{i}"
            row[name] = v

        # Remap to Loader-compatible field names
        clean: dict[str, str] = {
            "rank": row.get("rank", ""),
            "game_name": row.get("game_name", ""),
            "category": row.get("genre", ""),
            "developer": row.get("developer", ""),
        }
        # Optional extras
        if row.get("price"):
            clean["price"] = row["price"]
        if row.get("rating"):
            clean["rating"] = row["rating"]

        # Inject metadata
        clean["platform"] = platform
        clean["chart_type"] = chart_type
        clean["scrape_date"] = scrape_date

        result.append(clean)

    # Remove entirely empty columns
    if result:
        non_empty = [k for k in result[0] if any(r.get(k, "") != "" for r in result)]
        result = [{k: r[k] for k in non_empty} for r in result]

    return result


def scrape_one_chart(
    page,
    url_path: str,
    platform: str,
    chart_type: str,
    scrape_date: str,
    max_rows: int = MAX_ROWS,
) -> Path | None:
    """
    Scrape a single chart. Navigates to URL, scrolls, collects, saves CSV.

    Args:
        page: Playwright page object (already authenticated).
        url_path: URL path like "/rank/ios/1-4-0-75-2".
        platform: "iOS" | "Android".
        chart_type: "免费榜" | "畅销榜" | ...
        scrape_date: YYYY-MM-DD string.
        max_rows: Max rows to collect.

    Returns:
        Path to CSV file, or None if failed.
    """
    url = f"{BASE_URL}{url_path}"
    collected: dict[int, list[str]] = {}

    print(f"\n  → 打开: {chart_type} ({platform})")
    print(f"    URL: {url}")

    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(5_000)

    if "login" in page.url.lower():
        print("    ❌ 未登录，跳过")
        return None

    _switch_to_game_category(page)
    page.keyboard.press("Escape")
    page.wait_for_timeout(1_000)

    # Scroll & collect
    start = time.time()
    no_new_streak = 0

    for step in range(200):
        if step % POPUP_INTERVAL == 0:
            page.keyboard.press("Escape")

        before = len(collected)
        _collect_visible(page, collected)
        after = len(collected)

        if after > before:
            elapsed = time.time() - start
            max_rank = max(collected.keys())
            print(f"    [{elapsed:.0f}s] {after}/{max_rows} 条 (最高排名 #{max_rank})")
            no_new_streak = 0
        else:
            no_new_streak += 1

        if after >= max_rows:
            print(f"    ✅ 已达 {max_rows} 条")
            break

        if no_new_streak >= 4:
            print(f"    ⏹️ 连续无新数据，停止")
            break

        page.mouse.wheel(0, SCROLL_PX)
        time.sleep(SCROLL_PAUSE)

    elapsed = time.time() - start
    print(f"    ⏱️ 耗时: {elapsed:.0f}s | 收集: {len(collected)} 条")

    # Clean & save
    cleaned = _clean_rows(collected, platform, chart_type, scrape_date)
    if not cleaned:
        print(f"    ⚠️ 无有效数据")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    platform_slug = platform.lower()
    chart_slug = chart_type
    filename = f"{platform_slug}_{chart_slug}_{timestamp}.csv"
    csv_path = OUTPUT_DIR / filename

    import csv
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(cleaned[0].keys()))
        writer.writeheader()
        writer.writerows(cleaned)

    print(f"    📁 {csv_path} ({len(cleaned)} 条)")
    return csv_path


# ==================================================================
# 诊断模式：打印原始 DOM 列序
# ==================================================================

def diagnose() -> int:
    """
    Print raw DOM column layout for each chart type.
    Helps verify which column index = which data field.
    """
    if not CHROME_PROFILE.exists():
        print("❌ 未找到登录态！请先运行 python tools/diandian_auth.py")
        return 1

    # Sample one iOS chart of each type
    samples = {
        "ios_free":     ("/rank/ios/1-4-0-75-2", "iOS", "免费榜"),
        "ios_grossing": ("/rank/ios/1-4-0-75-3", "iOS", "畅销榜"),
        "ios_hot":      ("/rank/ios/1-4-0-75-4", "iOS", "热门榜"),
    }

    print("=" * 60)
    print("🔍 DOM 列序诊断")
    print("=" * 60)

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

            for key, (url_path, platform, chart_type) in samples.items():
                url = f"{BASE_URL}{url_path}"
                print(f"\n{'─'*60}")
                print(f"📊 {key}: {platform} {chart_type}")
                print(f"   URL: {url}")
                print(f"{'─'*60}")

                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_timeout(5_000)

                if "login" in page.url.lower():
                    print("   ❌ 未登录")
                    continue

                _switch_to_game_category(page)
                page.keyboard.press("Escape")
                page.wait_for_timeout(1_000)

                # Scroll a bit to ensure rows are rendered
                page.mouse.wheel(0, 300)
                page.wait_for_timeout(1_500)

                # Extract raw rows with ALL text nodes, preserving order
                raw_rows: list[list[str]] = page.evaluate("""
                    () => {
                        const results = [];
                        const rowElements = document.querySelectorAll('[class*="row"]');
                        for (const el of rowElements) {
                            if (el.offsetHeight === 0) continue;
                            const texts = [];
                            const walk = (node) => {
                                if (node.nodeType === 3) {
                                    const t = node.textContent.trim();
                                    if (t && t.length < 80) texts.push(t);
                                } else if (node.nodeType === 1) {
                                    const tag = node.tagName.toLowerCase();
                                    if (['span','div','p','td','th','a','li'].includes(tag) && node.children.length === 0) {
                                        const t = node.textContent.trim();
                                        if (t && t.length < 80) texts.push(t);
                                    } else {
                                        for (const child of node.children) walk(child);
                                    }
                                }
                            };
                            walk(el);
                            if (texts.length >= 3 && /^\\d+$/.test(texts[0])) results.push(texts);
                            if (results.length >= 5) break;
                        }
                        return results;
                    }
                """)

                for row_idx, values in enumerate(raw_rows):
                    print(f"\n   Row {row_idx + 1} ({len(values)} 列):")
                    for col_idx, val in enumerate(values):
                        print(f"      [{col_idx:2d}] {val}")

            context.close()

    except Exception as e:
        print(f"\n❌ 诊断出错: {e}")
        traceback.print_exc()
        return 1

    print(f"\n{'='*60}")
    print("✅ 诊断完成。对照上方列序修正 _clean_rows 的 COL_NAMES。")
    return 0


# ==================================================================
# 批量抓取
# ==================================================================

def batch_scrape(chart_keys: list[str]) -> dict[str, Any]:
    """
    Scrape multiple charts in one browser session.

    Args:
        chart_keys: List of chart keys from KNOWN_CHARTS (e.g. ["ios_free", "ios_grossing"]).

    Returns:
        Summary dict with results per chart.
    """
    if not CHROME_PROFILE.exists():
        return {"error": "未找到登录态！请先运行 python tools/diandian_auth.py"}

    charts_to_scrape = {k: KNOWN_CHARTS[k] for k in chart_keys if k in KNOWN_CHARTS}
    if not charts_to_scrape:
        return {"error": f"未找到匹配的榜单: {chart_keys}"}

    scrape_date = datetime.now().strftime("%Y-%m-%d")
    results: dict[str, Any] = {"date": scrape_date, "charts": {}}

    print("=" * 60)
    print("📊 点点数据 - 批量抓取")
    print(f"   日期: {scrape_date}")
    print(f"   榜单: {len(charts_to_scrape)} 个")
    print(f"   Chrome 配置: {CHROME_PROFILE}")
    print("=" * 60)

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

            for key, cfg in charts_to_scrape.items():
                platform = cfg["platform"]
                chart_type = cfg["chart_type"]
                url_path = cfg["url_path"]

                try:
                    csv_path = scrape_one_chart(page, url_path, platform, chart_type, scrape_date)
                    results["charts"][key] = {
                        "platform": platform,
                        "chart_type": chart_type,
                        "status": "ok" if csv_path else "empty",
                        "file": str(csv_path) if csv_path else None,
                    }
                except Exception as e:
                    print(f"    ❌ 抓取失败: {e}")
                    results["charts"][key] = {
                        "platform": platform,
                        "chart_type": chart_type,
                        "status": "error",
                        "error": str(e),
                    }

            context.close()

    except Exception as e:
        print(f"\n❌ 批量抓取出错: {e}")
        traceback.print_exc()
        results["error"] = str(e)

    # Summary
    ok_count = sum(1 for c in results["charts"].values() if c["status"] == "ok")
    empty_count = sum(1 for c in results["charts"].values() if c["status"] == "empty")
    error_count = sum(1 for c in results["charts"].values() if c["status"] == "error")

    print(f"\n{'='*60}")
    print(f"📋 批量抓取完成")
    print(f"   ✅ 成功: {ok_count}  |  ⚠️ 空数据: {empty_count}  |  ❌ 失败: {error_count}")
    print(f"{'='*60}")

    return results


# ==================================================================
# CLI
# ==================================================================

def main():
    parser = argparse.ArgumentParser(description="点点数据 - 多榜单批量抓取")
    parser.add_argument(
        "--discover", action="store_true",
        help="发现模式：打开页面，提取所有可用榜单的 URL 和切换方式"
    )
    parser.add_argument(
        "--diagnose", action="store_true",
        help="诊断模式：打印每种榜单的原始 DOM 列序，用于修正列映射"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="抓取所有已知榜单（iOS + Android 全 10 个）"
    )
    parser.add_argument(
        "--charts", type=str, default="",
        help="指定榜单 key，逗号分隔。如: ios_free,android_hot"
    )
    parser.add_argument(
        "--platform", type=str, default="", choices=["ios", "android"],
        help="只抓取指定平台的全部榜单"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="列出所有已知榜单的 key 和说明"
    )
    args = parser.parse_args()

    # --list: 列出可用榜单
    if args.list:
        print("已知榜单配置:\n")
        for key, cfg in sorted(KNOWN_CHARTS.items()):
            print(f"  {key:25s} → {cfg['platform']:8s} {cfg['chart_type']}")
        print(f"\n共 {len(KNOWN_CHARTS)} 个榜单")
        print("\n用法:")
        print("  python tools/scrapers/diandian_batch.py --discover   # 发现更多榜单")
        print("  python tools/scrapers/diandian_batch.py --all         # 抓取全部")
        print("  python tools/scrapers/diandian_batch.py --charts ios_free,ios_grossing")
        return

    # --discover: 发现模式
    if args.discover:
        sys.exit(discover())

    if args.diagnose:
        sys.exit(diagnose())

    # 确定要抓取哪些榜单
    if args.all:
        chart_keys = list(KNOWN_CHARTS.keys())
    elif args.platform:
        chart_keys = [k for k, v in KNOWN_CHARTS.items() if v["platform"].lower() == args.platform]
        if not chart_keys:
            print(f"未找到平台 '{args.platform}' 的榜单配置")
            sys.exit(1)
    elif args.charts:
        chart_keys = [k.strip() for k in args.charts.split(",")]
    else:
        print("请指定 --all, --platform, 或 --charts")
        print("使用 --list 查看可用榜单")
        parser.print_help()
        sys.exit(1)

    print(f"将抓取 {len(chart_keys)} 个榜单: {chart_keys}\n")

    result = batch_scrape(chart_keys)
    if "error" in result:
        print(f"\n❌ {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
