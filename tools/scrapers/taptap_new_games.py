"""
TapTap New Games Scraper — extracts upcoming/new game releases from TapTap app-calendar.

Data source: https://www.taptap.cn/app-calendar
Method: Parse embedded JSON-LD (Schema.org Events) + Nuxt.js state (application/json)
         for structured game data — no Playwright needed.

Output: Standard CSV at data/raw/tapTap_新品榜_YYYYMMDD.csv

Usage:
    python -m tools.scrapers.taptap_new_games
    python -m tools.scrapers.taptap_new_games --date 2026-06-22
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Fix import path for running as script or module
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.scrapers.base import ChartScraper

TAPTAP_URL = "https://www.taptap.cn/app-calendar"
TAPTAP_APP_BASE = "https://www.taptap.cn/app"

# Limit scraping to avoid hitting rate limits
MAX_GAMES = 30


class TapTapNewGames(ChartScraper):
    """Scrape TapTap app-calendar for upcoming/new game releases."""

    platform = "Android"
    chart_type = "新品榜"
    source_name = "TapTap-新游日历"

    # Extra columns beyond base STANDARD_COLUMNS
    EXTRA_COLUMNS = ["taptap_url", "downloads", "rating", "tags", "release_date", "event_type"]

    # Map scraper-native columns → internal field names
    column_map: dict[str, str] = {
        "rank": "rank",
        "game_name": "game_name",
        "developer": "developer",
        "category": "category",
        "taptap_url": "taptap_url",
        "downloads": "downloads",
        "rating": "rating",
        "tags": "tags",
        "release_date": "release_date",
        "event_type": "event_type",
        "description": "description",
        "reserve_count": "reserve_count",
    }

    # Map scraper-native columns → internal field names

    def scrape(self) -> list[dict[str, Any]]:
        """Fetch and parse TapTap app-calendar page.

        Returns list of raw game dicts with scraper-native column names.
        """
        client = self._get_client()
        print(f"  Fetching {TAPTAP_URL} ...")
        resp = client.get(TAPTAP_URL)
        resp.raise_for_status()
        html = resp.text
        print(f"  OK, {len(html)} bytes")

        # ── Step 1: Parse JSON-LD events (calendar entries) ──
        events = self._extract_jsonld_events(html)
        print(f"  JSON-LD events: {len(events)}")

        # ── Step 2: Parse Nuxt state for rich app data ──
        app_data_map = self._extract_nuxt_app_data(html)
        print(f"  Nuxt app cards: {len(app_data_map)}")

        # ── Step 3: Merge events with app data ──
        games: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        # First pass: process JSON-LD events (these have date ranges)
        for evt in events:
            app_id = self._extract_app_id(evt.get("url", ""))
            if app_id and app_id not in seen_ids:
                seen_ids.add(app_id)
                app_info = app_data_map.get(app_id, {})
                games.append(self._merge_event_and_app(evt, app_info, app_id))

        # Second pass: add any apps from Nuxt state not in events
        for app_id, app_info in app_data_map.items():
            if app_id not in seen_ids and len(games) < MAX_GAMES:
                seen_ids.add(app_id)
                try:
                    games.append(self._app_to_game(app_info, app_id))
                except Exception as e:
                    print(f"  [WARN] Failed to convert app {app_id}: {e}")

        print(f"  Total games: {len(games)}")
        return games[:MAX_GAMES]

    # ── JSON-LD Extraction ─────────────────────────────────────

    def _extract_jsonld_events(self, html: str) -> list[dict[str, Any]]:
        """Extract Schema.org Event objects from JSON-LD script blocks."""
        blocks = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        events: list[dict[str, Any]] = []
        for block in blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict) and data.get("@type") == "Event":
                    events.append(data)
            except json.JSONDecodeError:
                pass
        return events

    # ── Nuxt State Extraction ──────────────────────────────────

    def _extract_nuxt_app_data(self, html: str) -> dict[int, dict[str, Any]]:
        """Parse Nuxt state for per-app detail cards.

        Instead of trying to fully deep-resolve the Nuxt reference graph
        (which has cycles), we do targeted extraction: find app cards,
        then extract only the fields we need (title, rating, tags, stats)
        by following references precisely.

        Returns a dict mapping app_id → {title, rating, tags_str, downloads, ...}.
        """
        blocks = re.findall(
            r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )

        for block in blocks:
            try:
                data = json.loads(block)
                if isinstance(data, list):
                    return self._extract_apps_from_nuxt(data)
            except json.JSONDecodeError:
                continue

        return {}

    def _extract_apps_from_nuxt(
        self, data: list[Any]
    ) -> dict[int, dict[str, Any]]:
        """Walk the Nuxt flat array and extract app data with targeted ref resolution.

        Nuxt format: flat array where integers in dicts/lists are indices
        into the same array (reference de-duplication).

        Key structures:
          App card: {id, title, icon, stat: int, tags: int, hints: int, ...}
          Stat: {rating: {score: int→str}, hits_total: int, reserve_count: int, ...}
          Tags: int → [int→{value: int→str, web_url: int→str}, ...]
        """
        # Helper: resolve integer ref (1 level, no recursion into nested refs)
        def ref(idx: Any) -> Any:
            if isinstance(idx, int) and 0 <= idx < len(data):
                return data[idx]
            return idx

        app_map: dict[int, dict[str, Any]] = {}

        for item in data:
            if not isinstance(item, dict):
                continue
            if not ("id" in item and "title" in item and "icon" in item and "stat" in item):
                continue

            app_id = ref(item["id"])  # id is a Nuxt reference to the actual app ID
            if not isinstance(app_id, int) or app_id <= 0:
                continue
            title = ref(item["title"])  # title is also a reference
            if not isinstance(title, str) or not title.strip():
                continue

            # ── Resolve stat (with all sub-fields resolved) ──
            stat_raw = ref(item.get("stat"))
            if not isinstance(stat_raw, dict):
                stat_raw = {}
            # Resolve all integer references inside stat
            stat: dict[str, Any] = {}
            for sk, sv in stat_raw.items():
                stat[sk] = ref(sv)

            # Rating: stat.rating.score → resolve nested refs to get the score string
            rating = None
            rating_raw = stat.get("rating", {})
            if isinstance(rating_raw, dict):
                # rating_raw values may still be integer refs → resolve them
                resolved_rating: dict[str, Any] = {}
                for rk, rv in rating_raw.items():
                    resolved_rating[rk] = ref(rv)
                score = resolved_rating.get("score", resolved_rating.get("latest_score", 0))
                try:
                    rating = float(score)
                except (ValueError, TypeError):
                    pass

            # Stats — these are now resolved integers
            hits_total = stat.get("hits_total", 0)
            reserve_count = stat.get("reserve_count", 0)

            # ── Resolve tags ──
            tags_list: list[str] = []
            tags_raw = ref(item.get("tags"))
            if isinstance(tags_raw, list):
                for tag_ref in tags_raw[:8]:  # Cap at 8 tags
                    tag_dict = ref(tag_ref)
                    if isinstance(tag_dict, dict):
                        tag_value = ref(tag_dict.get("value"))
                        if isinstance(tag_value, str) and tag_value.strip():
                            tags_list.append(tag_value)

            # ── Resolve hints (developer info) ──
            developer = ""
            hints = item.get("hints")
            if isinstance(hints, int):
                hints_list = ref(hints)
                if isinstance(hints_list, list):
                    for hint_ref in hints_list[:2]:
                        hint_dict = ref(hint_ref)
                        if isinstance(hint_dict, dict):
                            hint_val = ref(hint_dict.get("value"))
                            if isinstance(hint_val, str) and hint_val.strip():
                                developer = hint_val
                                break

            # ── Downloads text ──
            downloads = ""
            reserve_str = ""
            if isinstance(reserve_count, (int, float)) and reserve_count > 0:
                reserve_str = str(int(reserve_count))
                rc = int(reserve_count)
                downloads = f"{rc // 10000}万+预约" if rc >= 10000 else f"{rc}预约"
            elif isinstance(hits_total, (int, float)) and hits_total > 0:
                ht = int(hits_total)
                downloads = f"{ht // 10000}万+关注" if ht >= 10000 else f"{ht}关注"

            # ── Category from first tag or show_module ──
            category = tags_list[0] if tags_list else ""
            if not category:
                show_module_raw = ref(item.get("show_module"))
                if isinstance(show_module_raw, list):
                    for sm_ref in show_module_raw[:2]:
                        sm = ref(sm_ref)
                        if isinstance(sm, dict):
                            sm_key = ref(sm.get("key"))
                            if isinstance(sm_key, str) and sm_key.strip():
                                category = sm_key
                                break

            app_map[app_id] = {
                "game_name": title,
                "developer": developer,
                "category": category,
                "tags_str": "|".join(tags_list),
                "rating": rating,
                "downloads": downloads,
                "reserve_count": reserve_str,
                "taptap_url": f"https://www.taptap.cn/app/{app_id}",
            }

        return app_map

    # ── Merging & Transformation ───────────────────────────────

    def _extract_app_id(self, url: str) -> int | None:
        """Extract numeric app ID from TapTap URL like /app/281223."""
        m = re.search(r"/app/(\d+)", url)
        return int(m.group(1)) if m else None

    def _merge_event_and_app(
        self,
        event: dict[str, Any],
        app_info: dict[str, Any],
        app_id: int,
    ) -> dict[str, Any]:
        """Merge JSON-LD event data with Nuxt app card data."""
        game = self._app_to_game(app_info, app_id)

        # Overlay event-specific fields
        game["release_date"] = self._parse_date(event.get("startDate", ""))
        game["event_type"] = self._infer_event_type(event)
        game["description"] = event.get("description", "")

        # If app_info didn't have a title (unlikely), use event name
        if not game.get("game_name"):
            game["game_name"] = event.get("name", "")

        return game

    def _app_to_game(
        self, app_info: dict[str, Any], app_id: int
    ) -> dict[str, Any]:
        """Convert extracted app data to scraper-native game dict.

        At this point app_info already has resolved fields from
        _extract_apps_from_nuxt: game_name, rating, tags_str, downloads, etc.
        """
        return {
            "rank": 0,
            "game_name": app_info.get("game_name", ""),
            "developer": app_info.get("developer", ""),
            "category": app_info.get("category", ""),
            "taptap_url": app_info.get("taptap_url", f"{TAPTAP_APP_BASE}/{app_id}"),
            "downloads": app_info.get("downloads", ""),
            "rating": app_info.get("rating") or "",
            "tags": app_info.get("tags_str", ""),
            "release_date": "",
            "event_type": "",
            "description": "",
            "reserve_count": app_info.get("reserve_count", ""),
        }

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _parse_date(iso_str: str) -> str:
        """Parse ISO 8601 date to YYYY-MM-DD."""
        if not iso_str:
            return ""
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            # Try simple date prefix
            return iso_str[:10] if len(iso_str) >= 10 else iso_str

    @staticmethod
    def _infer_event_type(event: dict[str, Any]) -> str:
        """Infer release event type from event data."""
        name = event.get("name", "")
        desc = event.get("description", "")
        combined = f"{name} {desc}"

        if "首发" in combined or "上线" in combined:
            return "首发"
        if "预约" in combined or "预" in combined or "测试" in combined:
            return "预约/测试"
        if "更新" in combined or "版本" in combined:
            return "版本更新"
        if "demo" in combined.lower() or "试玩" in combined:
            return "Demo"
        return "新游"


# ── Module-level convenience ──────────────────────────────────


def run_scrape(date: str | None = None) -> Path | None:
    """Run the TapTap scraper, save CSV, and populate taptap_new_games table."""
    scraper = TapTapNewGames()
    csv_path = scraper.run(date=date)

    if csv_path:
        # Also populate taptap_new_games table directly
        _sync_to_db(csv_path, date or datetime.now().strftime("%Y-%m-%d"))

    return csv_path


def _sync_to_db(csv_path: Path, date: str) -> None:
    """Read scraper CSV and insert/update taptap_new_games table."""
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
                rating_str = row.get("rating", "")
                try:
                    rating = float(rating_str) if rating_str else None
                except (ValueError, TypeError):
                    rating = None
                # Run track_filter to classify
                try:
                    from src.pipeline.track_filter import classify_game
                    tags_raw = row.get("tags", "")
                    tag_list = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
                    # Detect Steam port from tags
                    is_steam = any("steam" in t.lower() or "移植" in t for t in tag_list)
                    track_label = classify_game(
                        game_name=game_name,
                        genre=row.get("品类", ""),
                        tags=tag_list if tag_list else None,
                        description=row.get("description", ""),
                        is_steam_port=is_steam,
                    )
                    track_relevant = track_label == "track"
                except Exception:
                    track_relevant = False

                records.append({
                    "date": date,
                    "game_name": game_name,
                    "bundle_id": row.get("Bundle ID", "").replace("fallback:", ""),
                    "downloads": row.get("downloads", ""),
                    "rating": rating,
                    "tags": row.get("tags", ""),
                    "genre": row.get("品类", ""),
                    "description": row.get("description", ""),
                    "taptap_url": row.get("taptap_url", ""),
                    "track_relevant": track_relevant,
                })
            if records:
                db.insert_taptap_games(records)
                print(f"  [taptap] Synced {len(records)} games to taptap_new_games table")
    except Exception as e:
        print(f"  [WARN] DB sync failed: {e}")


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    csv_path = run_scrape()

    if csv_path:
        print(f"\nOutput: {csv_path}")
        import csv as _csv
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = _csv.DictReader(f)
            rows = list(reader)
        print(f"Games: {len(rows)}")
        for row in rows[:8]:
            name = row.get("应用", row.get("game_name", "?"))
            rating = row.get("rating", "")
            tags = row.get("tags", "")
            release = row.get("release_date", "")
            print(f"  {name:20s}  rating={rating}  tags={tags[:40]}  date={release}")
