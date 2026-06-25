"""
Bilibili Creator Monitor — track recent videos from followed UP主.

Uses Playwright with Chrome profile (like diandian_batch) to handle
Bilibili's login requirement + WBI signing. Intercepts the space API
response to get clean video JSON data.

Output: Standard CSV at data/raw/bilibili_creator_videos_YYYYMMDD.csv

Usage:
    # First run: login to Bilibili in the opened browser window
    python -m tools.scrapers.bilibili_creators

    # Subsequent runs: uses saved login state (headless)
    python -m tools.scrapers.bilibili_creators --headless

    # With custom creator list
    python -m tools.scrapers.bilibili_creators --creators my_creators.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from src.pipeline.source_constants import NewsSource

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CHROME_PROFILE = PROJECT_ROOT / "data" / ".bilibili_chrome_profile"
OUTPUT_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_CREATORS_YAML = PROJECT_ROOT / "data" / "bilibili_creators.yaml"

# ── Scraping parameters ──
MAX_FALLBACK_VIDEOS = 2         # When no videos in date range, take this many latest
MAX_PAGES = 3                   # Max pages of space API to paginate through (30 vids/page)
PAGE_TIMEOUT = 30_000           # ms
API_WAIT_TIMEOUT = 15_000       # ms for initial API response
SCROLL_PAUSE = 2_000            # ms between scrolls for lazy loading


class BilibiliCreatorScraper:
    """Scrape recent videos from configured Bilibili creators."""

    def __init__(
        self,
        output_dir: Path | None = None,
        creators: list[dict[str, str]] | None = None,
        headless: bool = False,
    ):
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.creators = creators or self._load_creators()
        self.headless = headless

    # ── Public API ──────────────────────────────────────────────

    def run(self, date: str | None = None) -> Path | None:
        """Scrape all creators, return path to output CSV.

        Logic:
          1. Scrape all recent videos from each creator (paginate up to MAX_PAGES).
          2. Filter to videos published on `date` or `yesterday`.
          3. If no videos in date range, fall back to the latest MAX_FALLBACK_VIDEOS.
          4. Remove videos already reported in previous runs (tracked by BVID).
          5. Save newly-reported BVIDs to tracking file.
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        # Calculate yesterday
        from datetime import timedelta
        today_dt = datetime.strptime(date, "%Y-%m-%d")
        yesterday_dt = today_dt - timedelta(days=1)
        yesterday = yesterday_dt.strftime("%Y-%m-%d")
        date_range = {date, yesterday}

        print(f"\n{'='*50}")
        print(f"[Bilibili] Creator Monitor — {date}")
        print(f"   Date range: {yesterday} ~ {date}")
        print(f"   Creators: {len(self.creators)}")
        print(f"   Headless: {self.headless}")
        print(f"{'='*50}")

        if not CHROME_PROFILE.exists():
            print("\n[!] First run: needs Bilibili login")
            print(f"   Chrome profile: {CHROME_PROFILE}")
            print("   浏览器窗口将打开，请手动扫码/账号登录后关闭浏览器")
            print("   下次运行将复用登录态\n")
            self.headless = False

        # Load previously reported BVIDs
        reported_bvids = self._load_reported_bvids()

        all_videos: list[dict[str, Any]] = []
        date_matched = 0
        fallback_used = 0

        for i, creator in enumerate(self.creators):
            uid = creator["uid"]
            label = creator.get("label", uid)
            print(f"\n── [{i+1}/{len(self.creators)}] {label} (UID: {uid}) ──")

            try:
                raw_videos = self._scrape_creator(uid, label)
                print(f"   Fetched: {len(raw_videos)} total from space API")

                # Filter to date range
                in_range = [
                    v for v in raw_videos
                    if v.get("created_at", "")[:10] in date_range
                ]

                if in_range:
                    print(f"   In date range: {len(in_range)} videos")
                    date_matched += len(in_range)
                    all_videos.extend(in_range)
                else:
                    # Fallback: take latest
                    fallback = raw_videos[:MAX_FALLBACK_VIDEOS]
                    print(f"   No videos in date range, fallback to latest {len(fallback)}")
                    fallback_used += len(fallback)
                    all_videos.extend(fallback)

            except Exception as e:
                print(f"   [FAIL] Error: {e}")
                traceback.print_exc()

            if i < len(self.creators) - 1:
                time.sleep(3)

        if not all_videos:
            print("\n[!] No videos collected from any creator")
            return None

        # Deduplicate by BVID within this run
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for v in all_videos:
            if v["bvid"] not in seen:
                seen.add(v["bvid"])
                unique.append(v)
        all_videos = unique

        # Filter out previously reported BVIDs
        fresh = [v for v in all_videos if v["bvid"] not in reported_bvids]
        skipped = len(all_videos) - len(fresh)
        if skipped:
            print(f"\n   Filtered {skipped} already-reported videos")

        if not fresh:
            print("\n[!] All videos already reported in previous runs")
            return None

        # Sort by created_ts descending (newest first)
        fresh.sort(key=lambda x: x.get("created_ts", 0), reverse=True)

        # Add date and rank
        for idx, v in enumerate(fresh):
            v["date"] = date
            v["rank"] = idx + 1

        csv_path = self._write_csv(fresh, date)
        print(f"\n[FILE] Output: {csv_path}")
        print(f"   Date range ({yesterday}~{date}): {date_matched} videos")
        if fallback_used:
            print(f"   Fallback (latest): {fallback_used} videos")
        print(f"   New (not reported before): {len(fresh)}")
        print(f"   Total in CSV: {len(fresh)}")

        # Sync to SQLite
        self._sync_to_db(fresh)

        # Mark as reported
        self._save_reported_bvids(fresh, date)

        return csv_path

    # ── Core scraping ───────────────────────────────────────────

    def _scrape_creator(self, uid: str, label: str) -> list[dict[str, Any]]:
        """Scrape recent videos from a creator's space page.

        Paginates through up to MAX_PAGES by scrolling to trigger lazy loading.
        All API responses are intercepted to collect video data.
        """
        from playwright.sync_api import sync_playwright

        api_data: list[dict[str, Any]] = []
        browser_cookies: dict[str, str] = {}

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(CHROME_PROFILE),
                headless=self.headless,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="zh-CN",
            )

            page = context.new_page()

            # ── Intercept ALL pages of the space video API ──
            def _on_response(response):
                url = response.url
                if "api.bilibili.com/x/space/wbi/arc/search" in url:
                    try:
                        body = response.json()
                        if body.get("code") == 0:
                            data = body.get("data", {})
                            vlist = data.get("list", {}).get("vlist", [])
                            if vlist:
                                api_data.extend(vlist)
                    except Exception:
                        pass

            page.on("response", _on_response)

            # ── Navigate to space page ──
            page.goto(
                f"https://space.bilibili.com/{uid}",
                wait_until="networkidle",
                timeout=PAGE_TIMEOUT,
            )

            page.wait_for_timeout(API_WAIT_TIMEOUT)

            # ── Paginate via scrolling ──
            for pg in range(1, MAX_PAGES):
                prev_count = len(api_data)
                # Scroll to bottom to trigger lazy load of next page
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(SCROLL_PAUSE)
                new_count = len(api_data)
                if new_count == prev_count:
                    break  # No new data loaded
                print(f"   Page {pg+1}: {new_count - prev_count} more videos (total: {new_count})")

            # ── Extract videos ──
            if api_data:
                videos = self._parse_api_videos(api_data, uid, label)
            else:
                print("   [fallback] API interception failed, parsing DOM...")
                videos = self._parse_dom_videos(page, uid, label)

            # ── Save cookies ──
            for c in context.cookies():
                browser_cookies[c["name"]] = c["value"]

            context.close()

        # Deduplicate by BVID within this creator
        seen_bvids: set[str] = set()
        unique_videos: list[dict[str, Any]] = []
        for v in videos:
            if v["bvid"] not in seen_bvids:
                seen_bvids.add(v["bvid"])
                unique_videos.append(v)
        videos = unique_videos

        # Sort newest first
        videos.sort(key=lambda x: x.get("created_ts", 0), reverse=True)

        # ── Enrich with full detail from video API ──
        videos = self._enrich_videos(videos, browser_cookies)

        return videos

    def _enrich_videos(
        self, videos: list[dict[str, Any]], cookies: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Fetch full description + stats from video detail API for each video."""
        if not videos:
            return videos

        import httpx

        client = httpx.Client(
            cookies=cookies,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.bilibili.com/",
            },
            timeout=15,
        )

        enriched: list[dict[str, Any]] = []
        for i, v in enumerate(videos):
            bvid = v["bvid"]
            try:
                resp = client.get(
                    f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
                )
                if resp.status_code == 200:
                    body = resp.json()
                    if body.get("code") == 0:
                        data = body.get("data", {})
                        stat = data.get("stat", {})

                        # Full description
                        v["description"] = data.get("desc", v.get("description", ""))

                        # Extra stats not available in space API
                        v["like_count"] = stat.get("like", 0)
                        v["favorite_count"] = stat.get("favorite", 0)
                        v["coin_count"] = stat.get("coin", 0)
                        v["share_count"] = stat.get("share", 0)

                        # Partition / category
                        v["category"] = data.get("tname", "")

                        # More precise duration and pubdate
                        duration_sec = data.get("duration", 0)
                        if duration_sec and not v.get("duration"):
                            mins, secs = divmod(duration_sec, 60)
                            v["duration"] = f"{mins:02d}:{secs:02d}"

                        pubdate_ts = data.get("pubdate", 0)
                        if pubdate_ts and not v.get("created_at"):
                            v["created_at"] = datetime.fromtimestamp(pubdate_ts).strftime(
                                "%Y-%m-%d %H:%M"
                            )
                            v["created_ts"] = pubdate_ts

                        # ── AI Chinese subtitle ──
                        aid = data.get("aid", 0)
                        cid = data.get("cid", 0)
                        if aid and cid:
                            try:
                                v["ai_subtitle"] = self._fetch_ai_subtitle(
                                    client, aid, cid, bvid
                                )
                            except Exception:
                                v["ai_subtitle"] = ""

                        # ── Tags ──
                        try:
                            v["tags"] = self._fetch_tags(client, bvid)
                        except Exception:
                            v["tags"] = ""

                    else:
                        print(f"   [warn] Detail API failed for {bvid}: code={body.get('code')}")
                else:
                    print(f"   [warn] Detail API HTTP {resp.status_code} for {bvid}")
            except Exception as e:
                print(f"   [warn] Detail API error for {bvid}: {e}")

            enriched.append(v)

            # Rate-limit: 0.3s between detail requests
            if i < len(videos) - 1:
                time.sleep(0.3)

        client.close()
        return enriched

    @staticmethod
    def _fetch_ai_subtitle(
        client, aid: int, cid: int, bvid: str
    ) -> str:
        """Fetch AI-generated Chinese subtitle text for a video.

        Bilibili auto-generates subtitles via ASR for most videos.
        Returns the full joined text, or empty string if unavailable.
        """
        player_url = (
            f"https://api.bilibili.com/x/player/wbi/v2"
            f"?aid={aid}&cid={cid}&bvid={bvid}"
        )
        resp = client.get(player_url)
        if resp.status_code != 200:
            return ""

        body = resp.json()
        if body.get("code") != 0:
            return ""

        subtitle_info = body.get("data", {}).get("subtitle", {})
        subtitles = subtitle_info.get("subtitles", [])

        for sub in subtitles:
            if sub.get("lan") == "ai-zh":
                subtitle_url = sub.get("subtitle_url", "")
                if not subtitle_url:
                    continue
                if subtitle_url.startswith("//"):
                    subtitle_url = "https:" + subtitle_url

                sub_resp = client.get(subtitle_url)
                if sub_resp.status_code != 200:
                    return ""

                segments = sub_resp.json().get("body", [])
                return " ".join(s.get("content", "") for s in segments)

        return ""

    @staticmethod
    def _fetch_tags(client, bvid: str) -> str:
        """Fetch video tags as comma-separated string."""
        resp = client.get(
            f"https://api.bilibili.com/x/tag/archive/tags?bvid={bvid}"
        )
        if resp.status_code != 200 or resp.json().get("code") != 0:
            return ""

        tags = resp.json().get("data", [])
        if not isinstance(tags, list):
            return ""

        names = [t.get("tag_name", "") for t in tags if t.get("tag_name")]
        return ", ".join(names)

    def _parse_api_videos(
        self, api_data: list[dict[str, Any]], uid: str, label: str
    ) -> list[dict[str, Any]]:
        """Parse video data from the intercepted API response."""
        videos: list[dict[str, Any]] = []
        for v in api_data:
            bvid = v.get("bvid", "")
            if not bvid:
                continue

            created_ts = v.get("created", 0)
            created_str = ""
            if created_ts:
                try:
                    created_str = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    created_str = str(created_ts)

            videos.append({
                "creator_uid": uid,
                "creator_label": label,
                "bvid": bvid,
                "title": v.get("title", ""),
                "description": v.get("description", ""),
                "url": f"https://www.bilibili.com/video/{bvid}",
                "cover": v.get("pic", ""),
                "play_count": v.get("play", 0),
                "comment_count": v.get("comment", 0),
                "video_review": v.get("video_review", 0),  # danmu count
                "duration": v.get("length", ""),
                "created_at": created_str,
                "created_ts": created_ts,
            })

        # Sort by created time, newest first
        videos.sort(key=lambda x: x.get("created_ts", 0), reverse=True)
        return videos

    def _parse_dom_videos(
        self, page, uid: str, label: str
    ) -> list[dict[str, Any]]:
        """Fallback: extract video data from the rendered DOM.

        Uses multiple strategies to find video cards, since Bilibili's
        DOM structure varies between old-space and new-space layouts.
        """
        import re as _re
        videos: list[dict[str, Any]] = []
        seen_bvids: set[str] = set()

        # Strategy 1: Find all <a> tags linking to /video/BV...
        video_links = page.locator('a[href*="/video/BV"]').all()
        print(f"   [dom] Found {len(video_links)} video links")

        for el in video_links:
            href = el.get_attribute("href") or ""
            bv_match = _re.search(r"BV[a-zA-Z0-9]+", href)
            if not bv_match:
                continue
            bvid = bv_match.group(0)
            if bvid in seen_bvids:
                continue
            seen_bvids.add(bvid)

            # Try to extract title from the link or its ancestors
            title = ""
            try:
                # Try title attribute on the link itself
                title = el.get_attribute("title") or ""
                if not title or len(title) < 4:
                    # Try walking up to parent container
                    parent = el
                    for _ in range(5):
                        parent = parent.locator("xpath=..")
                        try:
                            # Look for any element with a meaningful title
                            all_titled = parent.locator("[title]").all()
                            for t in all_titled:
                                ta = t.get_attribute("title")
                                if ta and len(ta) > 10 and ta != title:
                                    title = ta
                                    break
                        except Exception:
                            pass
                        if title and len(title) > 5:
                            break
            except Exception:
                pass

            # Fallback: use the link's own text
            if not title or len(title) < 4:
                try:
                    title = el.inner_text().strip()
                except Exception:
                    title = ""

            # Filter out purely numeric titles (durations/stats misidentified)
            if not title or _re.match(r'^[\d.:\s万]+$', title):
                continue

            videos.append({
                "creator_uid": uid,
                "creator_label": label,
                "bvid": bvid,
                "title": title[:200],
                "description": "",
                "url": f"https://www.bilibili.com/video/{bvid}",
                "cover": "",
                "play_count": 0,
                "comment_count": 0,
                "video_review": 0,
                "duration": "",
                "created_at": "",
                "created_ts": 0,
            })

        # Deduplicate by title
        seen_titles: set[str] = set()
        unique_videos = []
        for v in videos:
            key = v["title"][:40]
            if key not in seen_titles:
                seen_titles.add(key)
                unique_videos.append(v)

        return unique_videos

    # ── CSV output ──────────────────────────────────────────────

    CSV_COLUMNS = [
        "date", "creator_uid", "creator_label", "bvid", "title",
        "description", "url", "cover",
        "play_count", "comment_count", "video_review",
        "like_count", "favorite_count", "coin_count", "share_count",
        "duration", "category", "tags", "ai_subtitle", "created_at",
    ]

    def _write_csv(self, videos: list[dict[str, Any]], date: str) -> Path:
        """Write videos to standard CSV file."""
        import csv

        date_compact = date.replace("-", "")
        filename = f"bilibili_creator_videos_{date_compact}.csv"
        csv_path = self.output_dir / filename

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS, restval="")
            writer.writeheader()
            for v in videos:
                row = {k: v.get(k, "") for k in self.CSV_COLUMNS}
                writer.writerow(row)

        return csv_path

    # ── Reported BVID tracking (DB-backed) ──

    @staticmethod
    def _load_reported_bvids() -> set[str]:
        """Load set of BVIDs already reported, from DB."""
        try:
            from src.storage.sqlite import get_db
            return get_db().get_reported_keys(NewsSource.BILIBILI)
        except Exception:
            return set()

    @staticmethod
    def _save_reported_bvids(videos: list[dict[str, Any]], date: str) -> None:
        """Save newly-reported BVIDs to DB."""
        if not videos:
            return
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            bvids = {v["bvid"] for v in videos}
            n = db.mark_reported(bvids, NewsSource.BILIBILI, date)
            db.prune_reported(NewsSource.BILIBILI, max_age_days=30)
            print(f"   DB: marked {n} new reported BVIDs")
        except Exception as e:
            print(f"   [warn] DB reported save failed: {e}")

    @staticmethod
    def _sync_to_db(videos: list[dict[str, Any]]) -> None:
        """Sync scraped videos to the bilibili_videos SQLite table."""
        if not videos:
            return
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            count = db.insert_bilibili_videos(videos)
            print(f"   DB: synced {count} videos to bilibili_videos table")
        except Exception as e:
            print(f"   [warn] DB sync failed: {e}")

    # ── Creator list loading ────────────────────────────────────

    def _load_creators(self) -> list[dict[str, str]]:
        """Load creator list from YAML file, or create default if none exists."""
        if DEFAULT_CREATORS_YAML.exists():
            with open(DEFAULT_CREATORS_YAML, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "creators" in data:
                    return data["creators"]

        # Create default config
        _defaults = [
            {"uid": "473519710", "label": "steam情报局", "tags": ["steam", "pc游戏"]},
            {"uid": "15782465", "label": "steam游戏资讯", "tags": ["steam", "pc游戏"]},
        ]
        print(f"[NEW] Creating default creator config: {DEFAULT_CREATORS_YAML}")
        config = {
            "description": "Bilibili creators to monitor for game intelligence",
            "creators": _defaults,
        }
        with open(DEFAULT_CREATORS_YAML, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

        return _defaults


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Bilibili Creator Monitor — track recent videos from UP主"
    )
    parser.add_argument("--date", type=str, default=None, help="Date YYYY-MM-DD")
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Run browser in headless mode (requires prior login + may be blocked)",
    )
    parser.add_argument(
        "--creators",
        type=str,
        default=None,
        help="Path to custom creators YAML file",
    )
    args = parser.parse_args()

    # Load custom creators if specified
    creators = None
    if args.creators:
        with open(args.creators, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            creators = data if isinstance(data, list) else data.get("creators", data)

    headless = args.headless

    scraper = BilibiliCreatorScraper(creators=creators, headless=headless)
    csv_path = scraper.run(date=args.date)

    if csv_path:
        print(f"\n[OK] Done: {csv_path}")
        # Print summary
        import csv
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        print(f"   Total: {len(rows)} videos from {len(set(r['creator_uid'] for r in rows))} creators")
        for r in rows[:10]:
            print(f"   [{r['creator_label']}] {r['title'][:60]}  |  {r['play_count']} plays  |  {r['created_at']}")
    else:
        print("\n[FAIL] No data collected")
