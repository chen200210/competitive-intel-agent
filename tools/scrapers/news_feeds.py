"""
Game News Headlines Scraper — collects daily headlines from gaming news sites.

Sources:
  1. 17173 — https://news.17173.com/
  2. 3DM — https://www.3dmgame.com/news/
  3. 游戏陀螺 — https://www.youxituoluo.com/
  4. 游戏日报 — https://news.yxrb.net/
  5. GameLook — http://www.gamelook.com.cn/

All items are classified through track_filter for relevance tagging.

Output: Standard CSV at data/raw/news_资讯_YYYYMMDD.csv

Usage:
    python -m tools.scrapers.news_feeds
    python -m tools.scrapers.news_feeds --date 2026-06-22
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

# Fix import path for running as script or module
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.pipeline.source_constants import NewsSource

from tools.scrapers.base import ChartScraper

# ── Config ───────────────────────────────────────────────────────
MAX_HEADLINES_PER_SOURCE = 20    # Max headlines per news site


class NewsFeeds(ChartScraper):
    """Scrape gaming news headlines from multiple sources."""

    platform = "全平台"
    chart_type = "资讯"
    source_name = "游戏资讯头条"

    # Extra columns beyond base STANDARD_COLUMNS
    EXTRA_COLUMNS = [
        "headline", "source", "url", "news_category",
        "related_game", "track_relevant", "publish_date",
    ]

    # headline → game_name so base._clean() won't skip news rows
    column_map: dict[str, str] = {
        "rank": "rank",
        "headline": "game_name",
        "news_category": "category",
        "source": "source",
    }

    # ── Scrape ─────────────────────────────────────────────────

    def scrape(self) -> list[dict[str, Any]]:
        """Fetch headlines from all sources, deduplicate, classify.

        Returns list of news dicts with scraper-native column names.
        """
        all_news: list[dict[str, Any]] = []

        # ═══ Source 1: 17173 ═══
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

        # ═══ Source 5: 游戏日报 ═══
        print("── 游戏日报 (news.yxrb.net) ──")
        try:
            news_yxrb = self._scrape_yxrb()
            print(f"  {len(news_yxrb)} 条头条")
            all_news.extend(news_yxrb)
        except Exception as e:
            print(f"  [WARN] 游戏日报抓取失败: {e}")

        # ═══ Source 6: GameLook ═══
        print("── GameLook (gamelook.com.cn) ──")
        try:
            news_gamelook = self._scrape_gamelook()
            print(f"  {len(news_gamelook)} 条头条")
            all_news.extend(news_gamelook)
        except Exception as e:
            print(f"  [WARN] GameLook抓取失败: {e}")

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

            publish_date = self._extract_publish_date(a_tag, full_url)

            news.append({
                "rank": 0,
                "headline": title,
                "source": NewsSource.GAME_17173,
                "url": full_url,
                "news_category": "头条",
                "related_game": "",
                "publish_date": publish_date,
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
            # Filter: news article URLs — /news/YYYYMM/DDDDDDD.html or /news/DDDDD/
            if not re.search(r'/news/\d{5,}', href):
                continue
            if not self._is_gaming_headline(title):
                continue

            links_seen.add(href)
            full_url = href if href.startswith("http") else f"https://www.3dmgame.com{href}"

            publish_date = self._extract_publish_date(a_tag, full_url)

            news.append({
                "rank": 0,
                "headline": title,
                "source": NewsSource.GAME_3DM,
                "url": full_url,
                "news_category": "头条",
                "related_game": "",
                "publish_date": publish_date,
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

            publish_date = self._extract_publish_date(a_tag, full_url)

            news.append({
                "rank": 0,
                "headline": title,
                "source": NewsSource.GAME_TUOLUO,
                "url": full_url,
                "news_category": "头条",
                "related_game": "",
                "publish_date": publish_date,
            })
        return news

    # ── 游戏日报 ───────────────────────────────────────────────

    def _scrape_yxrb(self) -> list[dict[str, Any]]:
        url = "https://news.yxrb.net/"
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
            if not self._is_gaming_headline(title):
                continue
            links_seen.add(href)
            full_url = href if href.startswith("http") else f"https://news.yxrb.net{href}" if href.startswith("/") else href
            publish_date = self._extract_publish_date(a_tag, full_url)
            news.append({"rank": 0, "headline": title, "source": NewsSource.GAME_RIBAO,
                         "url": full_url, "news_category": "头条",
                         "related_game": "", "publish_date": publish_date})
            if len(news) >= MAX_HEADLINES_PER_SOURCE:
                break
        return news

    # ── GameLook ───────────────────────────────────────────────

    def _scrape_gamelook(self) -> list[dict[str, Any]]:
        url = "http://www.gamelook.com.cn/"
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
            if not self._is_gaming_headline(title):
                continue
            links_seen.add(href)
            full_url = href if href.startswith("http") else f"http://www.gamelook.com.cn{href}" if href.startswith("/") else href
            publish_date = self._extract_publish_date(a_tag, full_url)
            news.append({"rank": 0, "headline": title, "source": NewsSource.GAME_LOOK,
                         "url": full_url, "news_category": "头条",
                         "related_game": "", "publish_date": publish_date})
            if len(news) >= MAX_HEADLINES_PER_SOURCE:
                break
        return news

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
            "club.gamersky.com",  # community forum, not news
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

    # ── Publish date extraction ────────────────────────────────

    @staticmethod
    def _extract_date_from_text(text: str) -> str:
        """Try to extract a YYYY-MM-DD or similar date from a text string.

        Returns 'YYYY-MM-DD' on success, empty string otherwise.
        Only accepts dates within 2024–2027 to filter out garbage.
        """
        # Pattern: 2026-06-23 or 2026/06/23 or 2026.06.23
        m = re.search(r'(20[2-9]\d)[-/.](\d{1,2})[-/.](\d{1,2})', text)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        # Pattern: 06月23日 or 6月23日 (Chinese, assume current year)
        m = re.search(r'(\d{1,2})月(\d{1,2})日', text)
        if m:
            today = datetime.now()
            return f"{today.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        return ""

    @staticmethod
    def _extract_date_from_url(url: str) -> str:
        """Try to extract a date from a news article URL.

        Supports patterns like:
          - /news/202606/1234567.html   (3DM, 17173)
          - /2026/0623/xxx.html         (full date in path)
          - /20260623/xxx               (compact date)
          - /2026/02/586962/            (GameLook: YYYY/MM/article_id)
          - /2026/02/586962.html        (GameLook variant with .html)
        """
        # /news/202606/... → 2026-06
        m = re.search(r'/(20[2-9]\d)(\d{2})/', url)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        # /2026/0623/... → 2026-06-23
        m = re.search(r'/(20[2-9]\d)/(\d{2})(\d{2})/', url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        # /2026/02/586962/ or /2026/02/586962.html → 2026-02 (GameLook pattern)
        m = re.search(r'/(20[2-9]\d)/(\d{2})/\d{4,}(?:\.html?)?', url)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        # /20260623... → 2026-06-23
        m = re.search(r'/(20[2-9]\d)(\d{2})(\d{2})\D', url)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        return ""

    @classmethod
    def _extract_publish_date(cls, a_tag, url: str) -> str:
        """Extract publish date from an <a> tag context.

        Strategy:
          1. Look for date text in parent/sibling elements near the <a> tag.
          2. Fall back to URL pattern extraction.
          3. Return empty string if nothing found.
        """
        # Strategy 1: Look for date in parent element
        parent = a_tag.parent
        if parent:
            parent_text = parent.get_text(strip=True)
            date_str = cls._extract_date_from_text(parent_text)
            if date_str:
                return date_str

        # Strategy 1b: Look in grandparent (common in list layouts)
        if parent and parent.parent:
            grandparent_text = parent.parent.get_text(strip=True)
            date_str = cls._extract_date_from_text(grandparent_text)
            if date_str:
                return date_str

        # Strategy 2: Look for <time> or date-classed elements nearby
        if parent:
            for selector in ["time", '[class*="time"]', '[class*="date"]',
                             "span.time", "em.date", "i.date"]:
                try:
                    date_el = parent.select_one(selector) if hasattr(parent, 'select_one') else None
                    if date_el:
                        date_str = cls._extract_date_from_text(date_el.get_text(strip=True))
                        if date_str:
                            return date_str
                except Exception as e:
                    print(f"  [WARN] 日期提取失败 (selector={selector}): {e}", file=sys.stderr)
                    pass

        # Strategy 3: URL pattern
        return cls._extract_date_from_url(url)

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
        except Exception as e:
            print(f"  [WARN] track_filter分类失败, 使用关键词兜底: {e}", file=sys.stderr)
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
    """Read scraper CSV, map to record dicts, insert with cross-day dedup."""
    import csv as _csv
    try:
        from src.storage.sqlite import get_db
        db = get_db()

        # ── Read CSV → standard record dicts ──
        records: list[dict[str, Any]] = []
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
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
                    "publish_date": row.get("publish_date", ""),
                })

        # ── Insert with cross-day URL dedup ──
        db.insert_market_news_deduped(records, date)

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
            marker = "[T]" if track == "1" else " - "
            print(f"  {marker} [{source}] {headline[:50]}  | {cat}")
    else:
        print("No news items collected.")
