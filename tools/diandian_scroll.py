"""
点点数据 - iOS 游戏免费榜滚动抓取。

不调 API，滚动 DOM 提取排行数据，存 CSV 到 OA 的 data/raw/ 目录。

用法:
    # 诊断模式：检查 DOM 结构是否匹配，不抓取
    python tools/diandian_scroll.py --check

    # 正常抓取
    python tools/diandian_scroll.py

输出:
    data/raw/ios_game_free_rank_YYYYMMDD_HHMMSS.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

# ---- 路径：共享 profile，输出到 OA 的 data/raw ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # OA/
CHROME_PROFILE = PROJECT_ROOT / "data" / ".diandian_chrome_profile"
OUTPUT_DIR = PROJECT_ROOT / "data" / "raw"

# ---- 榜单配置 ----
RANK_URL = "https://app.diandian.com/rank/ios/1-4-0-75-2?time=&device=1&timetype=[7]"
CHART_TYPE = "免费榜"  # iOS 游戏免费榜 — 与 Loader 的 chart_type 字段对应

# ---- 抓取参数 ----
MAX_ROWS = 200
SCROLL_PX = 250
SCROLL_PAUSE = 1.0
POPUP_INTERVAL = 8


def main() -> None:
    print("=" * 50)
    print("📊 iOS 游戏免费榜 - 滚动抓取")
    print(f"   Chrome 配置: {CHROME_PROFILE}")
    print(f"   输出目录:    {OUTPUT_DIR}")
    print("=" * 50)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    collected: dict[int, list[str]] = {}

    try:
        with sync_playwright() as p:
            # ---- 启动浏览器 ----
            print("\n→ 启动浏览器...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE),
                headless=False,
                viewport={"width": 1440, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            """)
            page = context.new_page()

            # ---- 打开页面 ----
            print("→ 打开榜单页...")
            page.goto(RANK_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(5_000)

            if "login" in page.url.lower():
                print("❌ 未登录！请先运行: python tools/diandian_auth.py")
                context.close()
                return

            # ---- 初始化 ----
            _switch_to_game_category(page)
            page.keyboard.press("Escape")
            page.wait_for_timeout(1_000)

            # ---- 滚动抓取 ----
            print(f"\n→ 开始滚动 (目标 {MAX_ROWS} 条)...")
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
                    print(f"  [{elapsed:.0f}s] {after}/{MAX_ROWS} 条 (最高排名 #{max_rank})")
                    no_new_streak = 0
                else:
                    no_new_streak += 1

                if after >= MAX_ROWS:
                    print(f"  ✅ 已达 {MAX_ROWS} 条")
                    break

                if no_new_streak >= 4:
                    print(f"  ⏹️ 连续无新数据，停止")
                    break

                page.mouse.wheel(0, SCROLL_PX)
                time.sleep(SCROLL_PAUSE)

            elapsed = time.time() - start
            print(f"\n⏱️ 耗时: {elapsed:.0f}s | 原始: {len(collected)} 条")

            # ---- 清洗 & 保存 ----
            scrape_date = datetime.now().strftime("%Y-%m-%d")
            cleaned = _clean_rows(collected, scrape_date)
            if cleaned:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_path = OUTPUT_DIR / f"ios_game_free_rank_{ts}.csv"
                with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=list(cleaned[0].keys()))
                    writer.writeheader()
                    writer.writerows(cleaned)
                print(f"📁 {csv_path} ({len(cleaned)} 条)")
            else:
                print("⚠️ 无数据")

            context.close()

    except Exception as e:
        print(f"\n❌ 出错: {e}")
        traceback.print_exc()
        input("\n按 Enter 退出...")

    print("\n✅ 完成!")


# ---------------------------------------------------------------------------
# 切换游戏分类
# ---------------------------------------------------------------------------
def _switch_to_game_category(page) -> None:
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


# ---------------------------------------------------------------------------
# 提取可见行
# ---------------------------------------------------------------------------
def _collect_visible(page, collected: dict[int, list[str]]) -> None:
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


# ---------------------------------------------------------------------------
# 列清洗
# ---------------------------------------------------------------------------
def _clean_rows(collected: dict[int, list[str]], scrape_date: str) -> list[dict[str, str]]:
    if not collected:
        return []

    col_counts = Counter(len(v) for v in collected.values())
    best_cols = col_counts.most_common(1)[0][0]

    # 列名与 Loader 的 COLUMN_ALIASES 对齐，确保可直接导入
    COL_NAMES = [
        "rank", "game_name", "category", "developer",
        "metric_a", "metric_b", "metric_c", "metric_d",
        "metric_e", "metric_f", "metric_g", "metric_h",
    ]

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
        # 注入固定字段
        row["platform"] = "iOS"
        row["chart_type"] = CHART_TYPE
        row["scrape_date"] = scrape_date
        result.append(row)

    # 删空列
    if result:
        non_empty = [k for k in result[0] if any(r.get(k, "") != "" for r in result)]
        result = [{k: r[k] for k in non_empty} for r in result]

    return result


# ---------------------------------------------------------------------------
# 诊断模式：验证 DOM 结构是否匹配
# ---------------------------------------------------------------------------
def check() -> int:
    """
    Open the ranking page and verify DOM structure without scraping.

    Returns exit code: 0 = all checks passed, 1 = checks failed.
    """
    print("=" * 50)
    print("🔍 点点数据 - DOM 结构诊断")
    print(f"   Chrome 配置: {CHROME_PROFILE}")
    print(f"   目标 URL:    {RANK_URL}")
    print("=" * 50)

    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    try:
        with sync_playwright() as p:
            # ---- 启动浏览器 ----
            print("\n→ 启动浏览器...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE),
                headless=False,
                viewport={"width": 1440, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            """)
            page = context.new_page()

            # ---- Check 1: Page load & auth ----
            print("\n[1/5] 页面加载 & 登录态...")
            page.goto(RANK_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(5_000)

            current_url = page.url.lower()
            if "login" in current_url:
                print("  ❌ 未登录！请先运行: python tools/diandian_auth.py")
                results.append({"check": "登录态", "status": "FAIL", "detail": "页面跳转到登录页"})
                failed += 1
            else:
                print("  ✅ 登录态有效")
                results.append({"check": "登录态", "status": "PASS", "detail": current_url})
                passed += 1

            # ---- Check 2: Page title ----
            print("\n[2/5] 页面标题...")
            title = page.title()
            print(f"  标题: '{title}'")
            if title and len(title) > 0:
                print("  ✅ 标题正常")
                results.append({"check": "页面标题", "status": "PASS", "detail": title})
                passed += 1
            else:
                print("  ⚠️ 标题为空（可能正常也可能页面加载不完整）")
                results.append({"check": "页面标题", "status": "WARN", "detail": "标题为空"})

            # ---- Check 3: Game category tab ----
            print("\n[3/5] 游戏分类标签...")
            found_tab = False
            for sel in ["text=游戏", "span:has-text('游戏')", "[class*='tab']:has-text('游戏')"]:
                try:
                    elem = page.locator(sel).first
                    if elem.is_visible(timeout=2_000):
                        text = elem.inner_text().strip()
                        print(f"  ✅ 找到标签: '{text}' (选择器: {sel})")
                        results.append({"check": "游戏分类标签", "status": "PASS", "detail": f"选择器 '{sel}' 匹配到 '{text}'"})
                        found_tab = True
                        passed += 1
                        break
                except Exception:
                    continue
            if not found_tab:
                print("  ❌ 未找到「游戏」标签")
                results.append({"check": "游戏分类标签", "status": "FAIL", "detail": "所有选择器均未匹配"})
                failed += 1

            # ---- Check 4: Row elements ----
            print("\n[4/5] 排行行元素...")
            row_count = page.evaluate("""
                () => document.querySelectorAll('[class*="row"]').length
            """)
            print(f"  [class*='row'] 匹配数: {row_count}")
            if row_count >= 10:
                print(f"  ✅ 找到 {row_count} 个行元素（足够）")
                results.append({"check": "排行行元素", "status": "PASS", "detail": f"{row_count} 个行元素"})
                passed += 1
            elif row_count > 0:
                print(f"  ⚠️ 仅有 {row_count} 个行元素，可能不够")
                results.append({"check": "排行行元素", "status": "WARN", "detail": f"仅 {row_count} 个行元素"})
            else:
                print("  ❌ 未找到排行行元素（DOM 结构可能已变化）")
                results.append({"check": "排行行元素", "status": "FAIL", "detail": "未找到 [class*='row'] 元素"})
                failed += 1

            # ---- Check 5: Sample row extraction ----
            print("\n[5/5] 样本行数据提取...")
            sample: list[list[str]] = page.evaluate("""
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
                        if (texts.length >= 2 && /^\\d+$/.test(texts[0])) results.push(texts);
                        if (results.length >= 3) break;
                    }
                    return results;
                }
            """)

            if sample:
                print(f"  ✅ 成功提取 {len(sample)} 条样本行:")
                for i, row in enumerate(sample):
                    print(f"     [{i+1}] rank={row[0]}, texts={row[1:4]}... (共 {len(row)} 列)")
                results.append({"check": "样本提取", "status": "PASS", "detail": f"成功提取 {len(sample)} 行，列数: {[len(r) for r in sample]}"})
                passed += 1
            else:
                print("  ❌ 未能提取到有效排行数据")
                print("     DOM 结构可能已变化，_collect_visible 的提取逻辑需要更新")
                results.append({"check": "样本提取", "status": "FAIL", "detail": "JS 提取逻辑未匹配到任何排行行"})
                failed += 1

            context.close()

    except Exception as e:
        print(f"\n❌ 诊断过程出错: {e}")
        traceback.print_exc()
        return 1

    # ---- Summary ----
    print("\n" + "=" * 50)
    print("📋 诊断报告")
    print("=" * 50)
    for r in results:
        icon = "✅" if r["status"] == "PASS" else ("⚠️" if r["status"] == "WARN" else "❌")
        print(f"  {icon} {r['check']}: {r['status']}")
        if r["status"] != "PASS":
            print(f"     → {r['detail']}")

    print(f"\n结果: {passed} 通过, {failed} 失败, {len(results) - passed - failed} 警告")
    if failed == 0:
        print("✅ 诊断通过！DOM 结构匹配，可以开始抓取。")
        return 0
    else:
        print("❌ 存在失败项。请检查上方的失败详情。")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="点点数据 iOS 游戏免费榜抓取")
    parser.add_argument(
        "--check", action="store_true",
        help="诊断模式：验证 DOM 结构是否匹配，不执行抓取"
    )
    args = parser.parse_args()

    if args.check:
        sys.exit(check())
    else:
        main()
