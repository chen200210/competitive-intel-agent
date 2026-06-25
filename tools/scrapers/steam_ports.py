"""
Steam Port Games Scraper — identifies Steam-to-mobile ports via dual-path strategy.

Primary path (方案 A): Reverse-lookup today's TapTap new games — for each game:
  1. Fetch TapTap page → analyze for Steam+port signals
  2. Three detection paths:
     a. TapTap Steam-integration JSON markers (most reliable)
     b. Textual port keywords ("移植", "steam移植", etc.)
     c. web_search fallback for non-TapTap games

Supplementary path (方案 B): web_search "Steam移植 手游" articles — cross-validate
  to catch games not listed on TapTap (e.g. Western indie ports).

Output: Standard CSV at data/raw/pc_手游_移植榜_YYYYMMDD.csv

Usage:
    python -m tools.scrapers.steam_ports
    python -m tools.scrapers.steam_ports --date 2026-06-22
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.scrapers.base import ChartScraper

# ── Config ──
MAX_TAPTAP_GAMES = 50
MAX_ARTICLE_GAMES = 10
SEARCH_DELAY = 1.5


class SteamPorts(ChartScraper):
    """Identify Steam-to-mobile ports via dual-path hybrid strategy."""

    platform = "PC_手游"
    chart_type = "移植榜"
    source_name = "Steam-移植手游"

    EXTRA_COLUMNS = ["is_steam_port", "source"]

    column_map: dict[str, str] = {
        "rank": "rank",
        "game_name": "game_name",
        "developer": "developer",
        "category": "category",
        "is_steam_port": "is_steam_port",
        "source": "source",
    }

    # TapTap Steam-integration JSON markers — TapTap embeds Steam price/rating/
    # review data only when a Steam App ID is linked to the mobile game page.
    # This is definitive evidence of a Steam→mobile port, even when the page
    # text doesn't use "移植" (common for games like 怪物火车2).
    STEAM_INTEGRATION_MARKERS = [
        "steam_review_with_comment",
        "steam_lowest_price",
        "steam_rank_with_comment",
        "steam_bar",
    ]

    # ═════════════════════════════════════════════════════════════
    # Scrape
    # ═════════════════════════════════════════════════════════════

    def scrape(self) -> list[dict[str, Any]]:
        games: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        # ── Primary Path: TapTap reverse-lookup ──
        print("── 主路径：TapTap 新游 → Steam 反向溯源 ──")
        taptap_games = self._get_taptap_games()
        limit = min(len(taptap_games), MAX_TAPTAP_GAMES)
        print(f"  TapTap 今日新游: {len(taptap_games)} 款, 检查前 {limit} 款")

        for i, game in enumerate(taptap_games[:MAX_TAPTAP_GAMES]):
            name = game.get("game_name", "")
            if not name or name in seen_names:
                continue

            print(f"  [{i + 1}/{limit}] 检查: {name}")
            taptap_url = game.get("taptap_url", "")
            result = self._check_steam_port(name, taptap_url)
            if result:
                result["source"] = "TapTap反向溯源"
                games.append(result)
                seen_names.add(name)
                print(f"    ✅ Steam 移植")
            else:
                print(f"    → 非 Steam 移植")

            if i < limit - 1:
                time.sleep(SEARCH_DELAY)

        # ── Supplementary Path: article search ──
        print(f"\n── 补充路径：web_search 汇总文章交叉验证 ──")
        extra_names = self._search_steam_port_articles()
        new_names = [n for n in extra_names if n not in seen_names]
        print(f"  文章命中 {len(extra_names)} 款游戏, 其中 {len(new_names)} 款不在 TapTap 中")

        for name in new_names[:MAX_ARTICLE_GAMES]:
            print(f"  检查: {name}")
            result = self._check_steam_port(name)
            if result:
                result["source"] = "文章汇总"
                games.append(result)
                seen_names.add(name)
                print(f"    ✅ Steam 移植")
            else:
                print(f"    → 无法确认为 Steam 移植")
            time.sleep(SEARCH_DELAY)

        for i, g in enumerate(games):
            g["rank"] = i + 1

        print(f"\n  总计: {len(games)} 款 Steam 移植手游")
        return games

    # ═════════════════════════════════════════════════════════════
    # Data sources
    # ═════════════════════════════════════════════════════════════

    def _get_taptap_games(self) -> list[dict[str, Any]]:
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            today = datetime.now().strftime("%Y-%m-%d")
            return db.get_taptap_games_by_date(today)
        except Exception as e:
            print(f"  [WARN] 无法读取 TapTap 数据: {e}")
            return []

    # ═════════════════════════════════════════════════════════════
    # Steam port detection
    # ═════════════════════════════════════════════════════════════

    def _check_steam_port(
        self, game_name: str, taptap_url: str = ""
    ) -> dict[str, Any] | None:
        """Check if a game is a Steam-to-mobile port.

        Detection paths (in order of confidence):
          1. TapTap Steam-integration JSON markers → definitive (Path A)
          2. TapTap page → textual port keywords + "steam" mention → probable (Path B)
          3. Fallback: web_search for port evidence (non-TapTap games only)

        Path B requires BOTH a port keyword AND "steam" on the page.
        "端游"+"移植" as separate mentions is no longer sufficient on its own —
        too many games describe themselves as "端游品质" and "移植了经典玩法"
        without being actual Steam→mobile ports.

        Returns minimal dict with game_name + is_steam_port=1, or None.
        """
        if taptap_url:
            has_port, has_steam = self._analyze_tap_page(taptap_url)
            if has_port and has_steam:
                return {
                    "rank": 0,
                    "game_name": game_name,
                    "developer": "",
                    "category": "",
                    "is_steam_port": 1,
                }

        # Fallback: web_search only for games without TapTap URL.
        # If TapTap analysis ran and returned False, trust that result —
        # web_search produces false positives for generic game names.
        if not taptap_url and self._web_search_port_evidence(game_name):
            return {
                "rank": 0,
                "game_name": game_name,
                "developer": "",
                "category": "",
                "is_steam_port": 1,
            }

        return None

    # ── TapTap page analysis ──

    def _analyze_tap_page(self, taptap_url: str) -> tuple[bool, bool]:
        """Analyze a TapTap game page for Steam and port signals.

        Returns (has_port_signal, has_steam_mention).

        Confidence tiers:
          - Path A: Steam-integration JSON markers → port signal regardless of text
          - Path B: Textual port keywords AND "steam" on the page → probable port
          - No signal: otherwise

        The weak signal (端游 + 移植 as separate mentions without "steam")
        is intentionally excluded — too many games use these words in unrelated
        contexts ("端游品质", "移植了经典玩法").
        """
        try:
            client = self._get_client()
            resp = client.get(taptap_url)
            resp.raise_for_status()
            page_text = resp.text
        except Exception:
            return False, False

        page_lower = page_text.lower()

        # ── Path A: TapTap Steam-integration markers (most reliable) ──
        has_steam_integration = any(m in page_lower for m in self.STEAM_INTEGRATION_MARKERS)

        # ── Steam mention on page ──
        has_steam = "steam" in page_lower

        # ── Path B: Textual port signals (require steam mention) ──
        specific_port_signals = [
            "steam移植", "pc移植",
            "从steam移植", "从pc移植",
            "steam原版", "pc原版",
            "已在steam发售",
            "端游移植",       # contiguous phrase — strong signal
        ]
        has_specific = any(s in page_lower for s in specific_port_signals)

        # 端游 + 移植 as co-occurring terms (weaker than contiguous phrase)
        has_duanyou = "端游" in page_text
        has_yizhi = "移植" in page_text
        has_duanyou_yizhi = has_duanyou and has_yizhi

        # ── Decision ──
        if has_steam_integration:
            # Path A: definitive — always a port signal
            has_port_signal = True
        elif has_steam:
            # Path B: textual signals + steam mention on page
            has_port_signal = has_specific or has_duanyou_yizhi
        else:
            # No steam mention at all — can't be a Steam port
            has_port_signal = False

        # ── Multi-platform signals (weaken port claim) ──
        # Only applies to Path B detections (not Steam integration)
        if has_port_signal and not has_steam_integration:
            multi_signals = ["双端上线", "多端上线", "同步上线", "同步发售", "全平台"]
            if any(s in page_text for s in multi_signals):
                port_count = sum(page_lower.count(s) for s in specific_port_signals)
                port_count += (1 if has_duanyou_yizhi else 0)
                multi_count = sum(page_text.count(s) for s in multi_signals)
                if multi_count >= port_count:
                    has_port_signal = False

        return has_port_signal, has_steam

    def _web_search_port_evidence(self, game_name: str) -> bool:
        """Fallback: use web_search to find Steam→mobile port evidence."""
        port_keywords = [
            "Steam移植", "steam移植", "PC移植", "端游移植",
            "从Steam移植",
        ]
        try:
            from src.tools.web_search import web_search
            result_str = web_search(
                f"{game_name} Steam 移植 手机", max_results=5,
            )
            result = json.loads(result_str)
            all_text = " ".join(
                f"{r.get('title', '')} {r.get('snippet', '')}"
                for r in result.get("results", [])
            )
            for kw in port_keywords:
                if kw.lower() in all_text.lower():
                    return True
        except Exception:
            pass
        return False

    # ═════════════════════════════════════════════════════════════
    # Supplementary: article search
    # ═════════════════════════════════════════════════════════════

    def _search_steam_port_articles(self) -> list[str]:
        queries = [
            "Steam移植 手游 2026 新作",
            "Steam游戏 手机版 移植",
        ]
        all_names: list[str] = []
        for query in queries:
            try:
                from src.tools.web_search import web_search
                result_str = web_search(query, max_results=5)
                result = json.loads(result_str)
            except Exception as e:
                print(f"  [WARN] 搜索 '{query}' 失败: {e}")
                continue
            for r in result.get("results", []):
                text = f"{r.get('title', '')} {r.get('snippet', '')}"
                all_names.extend(re.findall(r'《([^》]+)》', text))
                all_names.extend(re.findall(r'「([^」]+)」', text))
            time.sleep(SEARCH_DELAY)

        seen: set[str] = set()
        unique: list[str] = []
        for n in all_names:
            n = n.strip()
            if n and n not in seen and len(n) <= 30:
                seen.add(n)
                unique.append(n)
        return unique


# ═════════════════════════════════════════════════════════════
# Module-level convenience
# ═════════════════════════════════════════════════════════════

def run_scrape(date: str | None = None) -> Path | None:
    scraper = SteamPorts()
    csv_path = scraper.run(date=date)
    if csv_path:
        _sync_to_db(csv_path, date or datetime.now().strftime("%Y-%m-%d"))
    return csv_path


def _sync_to_db(csv_path: Path, date: str) -> None:
    import csv as _csv
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            records: list[dict[str, Any]] = []
            for row in reader:
                game_name = row.get("应用", row.get("game_name", ""))
                if not game_name:
                    continue
                records.append({
                    "date": date,
                    "game_name": game_name,
                    "steam_url": "",
                    "mobile_bundle_id": row.get("Bundle ID", "").replace("fallback:", ""),
                    "gameplay_tags": "",
                    "genre": row.get("品类", ""),
                    "has_mobile_version": True,
                    "track_relevant": True,
                })
            if records:
                # Clear today's old data before insert (removes false positives
                # from previous runs with looser detection criteria)
                with db._connect() as conn:
                    conn.execute(
                        "DELETE FROM steam_port_games WHERE date = ?", (date,)
                    )
                    conn.commit()
                db.insert_steam_ports(records)
                print(f"  [steam] Synced {len(records)} games to steam_port_games table")
    except Exception as e:
        print(f"  [WARN] DB sync failed: {e}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser(description="Steam Port Games Scraper")
    parser.add_argument("--date", type=str, default=None, help="Date YYYY-MM-DD")
    args = parser.parse_args()

    csv_path = run_scrape(date=args.date)

    if csv_path:
        print(f"\nOutput: {csv_path}")
        import csv as _csv
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            rows = list(reader)
        print(f"Games: {len(rows)}")
        for row in rows:
            name = row.get("应用", row.get("game_name", "?"))
            source = row.get("source", "")
            print(f"  {name:20s}  source={source}")
    else:
        print("No Steam port games found.")
