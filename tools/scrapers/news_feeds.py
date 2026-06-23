"""
Game News Headlines Scraper — collects daily headlines from gaming news sites.

Sources:
  1. GamerSky (游侠资讯) — https://www.gamersky.com/news/
  2. 17173 — https://news.17173.com/
  3. 3DM — https://www.3dmgame.com/news/
  4. 游戏陀螺 — https://www.youxituoluo.com/

All items are classified through track_filter for relevance tagging.

Output: Standard CSV at data/raw/news_资讯_YYYYMMDD.csv

Usage:
    python -m tools.scrapers.news_feeds
    python -m tools.scrapers.news_feeds --date 2026-06-22
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

# Fix import path for running as script or module
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.scrapers.base import ChartScraper

# ── Config ───────────────────────────────────────────────────────
MAX_HEADLINES_PER_SOURCE = 20    # Max headlines per news site
MAX_TRACK_SEARCH_RESULTS = 10    # Max results from track news search
SEARCH_DELAY = 1.5               # seconds between web_search calls
FETCH_TIMEOUT = 20               # seconds for HTTP fetch


class NewsFeeds(ChartScraper):
    """Scrape gaming news headlines from multiple sources."""

    platform = "全平台"
    chart_type = "资讯"
    source_name = "游戏资讯头条"

    # Extra columns beyond base STANDARD_COLUMNS
    EXTRA_COLUMNS = [
        "headline", "source", "url", "news_category",
        "related_game", "track_relevant",
    ]

    # headline → game_name so base._clean() won't skip news rows
    column_map: dict[str, str] = {
        "rank": "rank",
        "headline": "game_name",
        "news_category": "category",
        "source": "source",
    }

    def __init__(self, output_dir: Path | None = None):
        super().__init__(output_dir=output_dir)
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=httpx.Timeout(FETCH_TIMEOUT),
                follow_redirects=True,
            )
        return self._client

    def _clean(self, raw_rows: list[dict[str, Any]], date: str) -> list[dict[str, str]]:
        """Override to preserve extra news-specific columns."""
        cleaned = super()._clean(raw_rows, date)
        for raw, clean in zip(raw_rows, cleaned):
            for col in self.EXTRA_COLUMNS:
                val = raw.get(col, "")
                if val is not None and val != "":
                    clean[col] = str(val)
        return cleaned

    # ── Scrape ─────────────────────────────────────────────────

    def scrape(self) -> list[dict[str, Any]]:
        """Fetch headlines from all sources, deduplicate, classify.

        Returns list of news dicts with scraper-native column names.
        """
        all_news: list[dict[str, Any]] = []

        # ═══ Source 1: GamerSky ═══
        print("── 游侠资讯 (gamersky.com) ──")
        try:
            gamersky_news = self._scrape_gamersky()
            print(f"  {len(gamersky_news)} 条头条")
            all_news.extend(gamersky_news)
        except Exception as e:
            print(f"  [WARN] 游侠资讯抓取失败: {e}")

        # ═══ Source 2: 17173 ═══
        print("── 17173 资讯 (news.17173.com) ──")
        try:
            news17173 = self._scrape_17173()
            print(f"  {len(news17173)} 条头条")
            all_news.extend(news17173)
        except Exception as e:
            print(f"  [WARN] 17173 抓取失败: {e}")

        # ═══ Source 3: 3DM ═══
        print("── 3DM (3dmgame.com) ──")
        try:
            news_3dm = self._scrape_3dm()
            print(f"  {len(news_3dm)} 条头条")
            all_news.extend(news_3dm)
        except Exception as e:
            print(f"  [WARN] 3DM 抓取失败: {e}")

        # ═══ Source 4: 游戏陀螺 ═══
        print("── 游戏陀螺 (youxituoluo.com) ──")
        try:
            news_tuoluo = self._scrape_youxituoluo()
            print(f"  {len(news_tuoluo)} 条头条")
            all_news.extend(news_tuoluo)
        except Exception as e:
            print(f"  [WARN] 游戏陀螺抓取失败: {e}")
        except Exception as e:
            print(f"  [WARN] 赛道新闻搜索失败: {e}")

        # ═══ Deduplicate by URL ═══
        seen_urls: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in all_news:
            url = item.get("url", "")
            if url and url in seen_urls:
                continue
            seen_urls.add(url)
            unique.append(item)

        # ═══ Classify each item with track_filter ═══
        for item in unique:
            item["track_relevant"] = int(
                self._is_track_relevant(
                    item.get("headline", ""),
                    item.get("related_game", ""),
                )
            )

        # Assign ranks
        for i, item in enumerate(unique):
            item["rank"] = i + 1

        print(f"\n  总计: {len(unique)} 条新闻 (去重后)")
        track_count = sum(1 for n in unique if n.get("track_relevant"))
        print(f"  赛道相关: {track_count} 条")

        return unique

    # ── GamerSky ──────────────────────────────────────────────

    def _scrape_gamersky(self) -> list[dict[str, Any]]:
        """Scrape GamerSky news homepage for headline list.

        GamerSky uses a list layout. We try multiple common selectors
        to handle potential HTML changes gracefully.
        """
        url = "https://www.gamersky.com/news/"
        client = self._get_client()
        resp = client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        news: list[dict[str, Any]] = []
        links_seen: set[str] = set()

        # Strategy: find all <a> tags with href and non-empty text
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            title = a_tag.get_text(strip=True)

            if not title or len(title) < 6:
                continue
            # Filter out navigation boilerplate
            skip_titles = {"首页", "新闻", "资讯", "头条", "游戏", "17173首页", "游侠首页",
                           "Home", "News", "手机版", "客户端", "APP下载"}
            if title in skip_titles:
                continue
            if href in links_seen:
                continue

            # Filter: only gaming news URLs
            if not self._is_news_url(href, domain="gamersky.com"):
                continue

            # Filter out non-gaming headlines (sports, celebrity, etc.)
            if not self._is_gaming_headline(title):
                continue

            links_seen.add(href)
            full_url = href if href.startswith("http") else f"https:{href}" if href.startswith("//") else f"https://www.gamersky.com{href}" if href.startswith("/") else href

            news.append({
                "rank": 0,
                "headline": title,
                "source": "游侠资讯",
                "url": full_url,
                "news_category": "头条",
                "related_game": "",
            })

            if len(news) >= MAX_HEADLINES_PER_SOURCE:
                break

        return news

    # ── 17173 ─────────────────────────────────────────────────

    def _scrape_17173(self) -> list[dict[str, Any]]:
        """Scrape 17173 news homepage for headline list."""
        url = "https://news.17173.com/"
        client = self._get_client()
        resp = client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        news: list[dict[str, Any]] = []
        links_seen: set[str] = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            title = a_tag.get_text(strip=True)

            if not title or len(title) < 6:
                continue
            # Filter out navigation boilerplate
            skip_titles = {"首页", "新闻", "资讯", "头条", "游戏", "17173首页", "游侠首页",
                           "Home", "News", "手机版", "客户端", "APP下载"}
            if title in skip_titles:
                continue
            if href in links_seen:
                continue

            if not self._is_news_url(href, domain="17173.com"):
                continue

            # Filter out non-gaming headlines (sports, celebrity, etc.)
            if not self._is_gaming_headline(title):
                continue

            links_seen.add(href)
            full_url = href if href.startswith("http") else f"https:{href}" if href.startswith("//") else f"https://news.17173.com{href}" if href.startswith("/") else href

            news.append({
                "rank": 0,
                "headline": title,
                "source": "17173",
                "url": full_url,
                "news_category": "头条",
                "related_game": "",
            })

            if len(news) >= MAX_HEADLINES_PER_SOURCE:
                break

        return news

    # ── Track news search ─────────────────────────────────────

    # ── 3DM ───────────────────────────────────────────────────

    def _scrape_3dm(self) -> list[dict[str, Any]]:
        """Scrape 3DM news homepage."""
        url = "https://www.3dmgame.com/news/"
        client = self._get_client()
        resp = client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        news: list[dict[str, Any]] = []
        links_seen: set[str] = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            title = a_tag.get_text(strip=True)
            if not title or len(title) < 10:
                continue
            if href in links_seen:
                continue
            # Filter: news article URLs
            if not re.search(r'/news/\d{7,}', href):
                continue
            if not self._is_gaming_headline(title):
                continue

            links_seen.add(href)
            full_url = href if href.startswith("http") else f"https://www.3dmgame.com{href}"

            news.append({
                "rank": 0,
                "headline": title,
                "source": "3DM",
                "url": full_url,
                "news_category": "头条",
                "related_game": "",
            })
        return news

    # ── 游戏陀螺 ───────────────────────────────────────────────

    def _scrape_youxituoluo(self) -> list[dict[str, Any]]:
        """Scrape 游戏陀螺 homepage for game industry news."""
        url = "https://www.youxituoluo.com/"
        client = self._get_client()
        resp = client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        news: list[dict[str, Any]] = []
        links_seen: set[str] = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            title = a_tag.get_text(strip=True)
            if not title or len(title) < 10:
                continue
            if href in links_seen:
                continue
            # Filter: article URLs like /534598.html
            if not re.search(r'/\d{5,}\.html', href):
                continue
            if not self._is_gaming_headline(title):
                continue

            links_seen.add(href)
            full_url = f"https://www.youxituoluo.com{href}" if href.startswith("/") else href

            news.append({
                "rank": 0,
                "headline": title,
                "source": "游戏陀螺",
                "url": full_url,
                "news_category": "头条",
                "related_game": "",
            })
        return news

    def _search_track_news_via_360(self) -> list[dict[str, Any]]:
        """Search for track-related gaming news via 360 search.

        Uses simple keyword queries — no site: operator (unsupported in China).
        Results are filtered through _is_gaming_headline to remove garbage.
        """
        queries = [
            "塔防 手游 新闻 2026",
            "肉鸽 Roguelike 新游 2026",
            "塔防 游戏 更新 2026",
            "肉鸽 手游 新版本",
        ]
        all_news: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for query in queries:
            try:
                result_str = web_search(query, max_results=5)
                result = json.loads(result_str)
            except Exception:
                continue

            for r in result.get("results", []):
                url = r.get("url", "")
                title = r.get("title", "")
                if not url or not title or url in seen_urls:
                    continue
                if not self._is_gaming_headline(title):
                    continue
                seen_urls.add(url)
                all_news.append({
                    "rank": 0,
                    "headline": title,
                    "source": "赛道新闻",
                    "url": url,
                    "news_category": "赛道搜索",
                    "related_game": "",
                })
            time.sleep(0.3)  # anti-rate-limit

        return all_news

    def _search_track_news(self) -> list[dict[str, Any]]:
        """Search for track-related gaming news via web_search.

        Uses multiple queries to cover 塔防 and 肉鸽 angles.
        """
        queries = [
            "塔防 手游 新闻 2026",
            "肉鸽 手游 新闻 2026",
        ]
        all_items: list[dict[str, Any]] = []

        for query in queries:
            try:
                from src.tools.web_search import web_search
                result_str = web_search(query, max_results=5)
                result = json.loads(result_str)
            except Exception as e:
                print(f"  [WARN] 搜索 '{query}' 失败: {e}")
                continue

            for r in result.get("results", []):
                title = r.get("title", "")
                url = r.get("url", "")
                snippet = r.get("snippet", "")

                if not title:
                    continue

                # Try to extract a related game name from title/snippet
                related_game = self._extract_game_name(title, snippet)

                all_items.append({
                    "rank": 0,
                    "headline": title,
                    "source": "赛道新闻搜索",
                    "url": url,
                    "news_category": "赛道",
                    "related_game": related_game,
                })

            time.sleep(SEARCH_DELAY)

        return all_items

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _is_news_url(url: str, domain: str) -> bool:
        """Check if a URL looks like a news article on the given domain.

        Filters out section pages, ads, and non-article links.
        """
        if domain not in url:
            return False
        # Exclude common non-article paths
        skip_patterns = [
            "javascript:", "#", "mailto:",
            "/tag/", "/zhuanti/", "/topic/",
            "/login", "/register", "/app",
        ]
        for pat in skip_patterns:
            if pat in url:
                return False
        return True

    @staticmethod
    def _is_gaming_headline(title: str) -> bool:
        """Check if a headline is gaming-related.

        GamerSky and 17173 mix gaming news with general entertainment.
        Default-deny: a headline must have at least one gaming signal to pass.
        """
        # Game title markers — 《》 almost always wraps a game/movie/anime name
        has_bookmark = "《" in title and "》" in title

        # Strong gaming signals — any one of these means it's gaming content
        gaming_signals = [
            "游戏", "手游", "网游", "页游", "主机", "端游",
            "Steam", "steam", "Epic", "Xbox", "PlayStation", "PS5", "Switch",
            "NS版", "PC版", "Xbox版",
            "电竞", "战队",
            "上线", "公测", "内测", "开服", "版本更新", "资料片", "DLC",
            "销量", "评分", "评分解禁", "M站",
            "开发商", "发行商", "工作室",
            "新作", "续作", "重制版", "重制", "移植", "复刻",
            "试玩", "Demo", "demo", "预告", "发售", "预售", "预购",
            "国区", "低价区",
            "卡普空", "任天堂", "索尼", "微软", "SE", "万代",
            "暴雪", "EA", "育碧", "R星", "CDPR",
            "米哈游", "腾讯", "网易",
            "JRPG", "RPG", "FPS", "MOBA", "RTS",
            "开放世界", "魂系", "肉鸽", "Roguelike",
            "Mod", "mod", "汉化", "补丁",
            "显卡", "驱动", "帧数", "光追",
        ]

        for kw in gaming_signals:
            if kw.lower() in title.lower():
                return True

        # 《》 with no non-gaming context → likely a game
        if has_bookmark:
            non_gaming_context = [
                "世界杯", "足球", "篮球", "球迷", "球王", "破门", "逼平",
                "歌手", "清唱", "抄袭", "绯闻", "出轨", "恋情",
                "股票", "涨停", "跌停", "基金经理",
                "安室奈美惠", "张靓颖", "单依纯",
            ]
            for kw in non_gaming_context:
                if kw in title:
                    return False
            return True

        # No gaming signal and no game title marker → not gaming content
        return False

    @staticmethod
    def _extract_game_name(title: str, snippet: str) -> str:
        """Try to extract a game name from title/snippet using 《》 brackets."""
        text = f"{title} {snippet}"
        names = re.findall(r'《([^》]+)》', text)
        if names:
            return names[0]
        # Fallback: 「」
        names2 = re.findall(r'「([^」]+)」', text)
        return names2[0] if names2 else ""

    @staticmethod
    def _is_track_relevant(headline: str, related_game: str) -> bool:
        """Check if a news item is track-relevant using track_filter.

        Returns True if headline or related_game matches track keywords.
        """
        try:
            from src.pipeline.track_filter import classify_game
            result = classify_game(
                game_name=related_game or headline,
                genre="",
                description=headline,
            )
            return result == "track"
        except Exception:
            # Fallback: simple keyword check
            track_kw = ["塔防", "TD", "Tower Defense", "肉鸽", "Roguelike", "Roguelite"]
            combined = f"{headline} {related_game}".lower()
            return any(kw.lower() in combined for kw in track_kw)


# ── Module-level convenience ──────────────────────────────────


def run_scrape(date: str | None = None) -> Path | None:
    """Run the news feeds scraper, save CSV, populate market_news table.

    Args:
        date: Date string YYYY-MM-DD. Defaults to today.

    Returns:
        Path to the output CSV file, or None if no data collected.
    """
    scraper = NewsFeeds()
    csv_path = scraper.run(date=date)

    if csv_path:
        _sync_to_db(csv_path, date or datetime.now().strftime("%Y-%m-%d"))

    return csv_path


def _sync_to_db(csv_path: Path, date: str) -> None:
    """Read scraper CSV and insert/update market_news table."""
    import csv as _csv
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            records: list[dict[str, Any]] = []
            for row in reader:
                headline = row.get("headline", row.get("应用", ""))
                url = row.get("url", "")
                if not headline or not url:
                    continue
                track_str = row.get("track_relevant", "0")
                try:
                    track = bool(int(track_str)) if track_str else False
                except (ValueError, TypeError):
                    track = False
                records.append({
                    "date": date,
                    "headline": headline,
                    "source": row.get("source", ""),
                    "url": url,
                    "category": row.get("news_category", row.get("品类", "")),
                    "related_game": row.get("related_game", ""),
                    "track_relevant": track,
                })
            if records:
                db.insert_market_news(records)
                print(f"  📊 Synced {len(records)} news items to market_news table")
    except Exception as e:
        print(f"  [WARN] DB sync failed: {e}")


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser(description="Game News Feeds Scraper")
    parser.add_argument("--date", type=str, default=None, help="Date YYYY-MM-DD")
    args = parser.parse_args()

    csv_path = run_scrape(date=args.date)

    if csv_path:
        print(f"\nOutput: {csv_path}")
        import csv as _csv
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            rows = list(reader)
        print(f"News items: {len(rows)}")
        for row in rows[:10]:
            headline = row.get("headline", row.get("应用", "?"))
            source = row.get("source", "?")
            cat = row.get("news_category", row.get("品类", ""))
            track = row.get("track_relevant", "0")
            marker = "🔴" if track == "1" else "  "
            print(f"  {marker} [{source}] {headline[:50]}  | {cat}")
    else:
        print("No news items collected.")
