"""
Base class for all ranking scrapers.

Every scraper inherits from this class. The base handles:
  1. Column mapping from scraper-native → standard format (§7.5.2)
  2. Bundle ID fallback ("0" or empty → "fallback:{game_name}")
  3. CSV output with standard filename: {platform}_{chart_type}_{YYYYMMDD}.csv
  4. Returns the output file path for Loader to import

Usage (in a subclass):
    class DiandianIOSFree(ChartScraper):
        platform = "iOS"
        chart_type = "免费榜"

        def scrape(self) -> list[dict]:
            ...  # return raw rows with scraper-native column names

    scraper = DiandianIOSFree()
    csv_path = scraper.run()  # data/raw/ios_免费榜_20260617.csv
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

# Project root (OA/) relative to this file
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "raw"

# ── Standard output columns per DESIGN.md §7.5.2 ────────────────
# These are what Loader expects. Scrapers map their native columns
# into this standard set.
STANDARD_COLUMNS = [
    "排名",        # rank    — required
    "Bundle ID",   # bundle_id — optional (auto-fallback if empty)
    "应用",        # game_name — required
    "平台",        # platform — auto-injected from scraper config
    "榜单",        # chart_type — auto-injected from scraper config
    "品类",        # category — recommended
    "开发者",      # developer — recommended
]

# Internal field names corresponding to STANDARD_COLUMNS
FIELD_MAP = {
    "排名": "rank",
    "Bundle ID": "bundle_id",
    "应用": "game_name",
    "平台": "platform",
    "榜单": "chart_type",
    "品类": "category",
    "开发者": "developer",
}


class ChartScraper:
    """Base scraper — subclasses set platform/chart_type and implement scrape()."""

    # ── Override these in subclasses ──

    platform: str = ""       # "iOS" | "Android"
    chart_type: str = ""     # "免费榜" | "畅销榜" | "热门榜" | "下载榜" | "收入榜"
    source_name: str = ""    # human-readable label (e.g. "点点数据-iOS免费榜")

    # ── Column mapping ──
    # Subclasses define how their native column names map to internal field names.
    # Keys = scraper-native column names, Values = internal field name (rank / game_name / ...)
    #
    # Example: {"rank": "rank", "game": "game_name", "genre": "category", "publisher": "developer"}
    column_map: dict[str, str] = {}

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────

    def run(self, date: str | None = None) -> Path | None:
        """
        Execute the full scrape → clean → write pipeline.

        Args:
            date: Date string YYYY-MM-DD. Defaults to today.

        Returns:
            Path to the output CSV file, or None if no data was collected.
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        print(f"\n{'='*50}")
        print(f"📊 {self.source_name or self.__class__.__name__}")
        print(f"   榜单: {self.chart_type}  平台: {self.platform}  日期: {date}")
        print(f"{'='*50}")

        # 1. Let the subclass collect data
        raw_rows = self.scrape()
        if not raw_rows:
            print("⚠️ 未获取到任何数据")
            return None

        print(f"→ 原始数据: {len(raw_rows)} 行")

        # 2. Normalize to standard format
        cleaned = self._clean(raw_rows, date)
        if not cleaned:
            print("⚠️ 清洗后无有效数据")
            return None

        print(f"→ 清洗后: {len(cleaned)} 行")

        # 3. Write CSV
        csv_path = self._write_csv(cleaned)
        print(f"📁 输出: {csv_path}")
        print("✅ 完成!")

        return csv_path

    def scrape(self) -> list[dict[str, Any]]:
        """
        Collect raw ranking data. Override in subclass.

        Returns a list of dicts with scraper-native column names.
        Each dict is one game's ranking row.
        """
        raise NotImplementedError("Subclasses must implement scrape()")

    # ── Internal: normalization ─────────────────────────────────

    def _clean(self, raw_rows: list[dict[str, Any]], date: str) -> list[dict[str, str]]:
        """Map scraper-native columns → standard columns, inject metadata."""
        cleaned: list[dict[str, str]] = []

        for row in raw_rows:
            # Map native columns → internal field names via column_map
            mapped: dict[str, str] = {}
            for native_key, val in row.items():
                field = self.column_map.get(native_key, native_key)
                mapped[field] = str(val).strip() if val is not None else ""

            game_name = mapped.get("game_name", "")
            if not game_name:
                continue  # skip rows without a game name

            # Bundle ID fallback
            bundle_id = mapped.get("bundle_id", "")
            if bundle_id == "0" or not bundle_id:
                bundle_id = f"fallback:{game_name}"

            # Rank
            rank_str = mapped.get("rank", "0")
            try:
                rank = int(rank_str)
            except (ValueError, TypeError):
                rank = 0

            cleaned.append({
                "排名": str(rank),
                "Bundle ID": bundle_id,
                "应用": game_name,
                "平台": self.platform,
                "榜单": self.chart_type,
                "品类": mapped.get("category", ""),
                "开发者": mapped.get("developer", ""),
            })

        # Remove columns that are entirely empty
        if cleaned:
            populated_cols = [
                col for col in STANDARD_COLUMNS
                if any(r.get(col, "") != "" for r in cleaned)
            ]
            cleaned = [{k: r[k] for k in populated_cols} for r in cleaned]

        return cleaned

    # ── Internal: CSV output ────────────────────────────────────

    def _write_csv(self, rows: list[dict[str, str]]) -> Path:
        """Write cleaned rows to a CSV file with standard naming."""
        date_str = datetime.now().strftime("%Y%m%d")
        # Sanitize platform and chart_type for filenames
        platform_slug = self.platform.lower().replace(" ", "_")
        chart_slug = self.chart_type

        filename = f"{platform_slug}_{chart_slug}_{date_str}.csv"
        csv_path = self.output_dir / filename

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        return csv_path


# ── CLI test entry ───────────────────────────────────────────────

if __name__ == "__main__":
    print("ChartScraper base class — no data source configured.")
    print("Create a subclass with platform/chart_type and implement scrape().")
    print()
    print("Standard output columns:", STANDARD_COLUMNS)
    print("Output directory:", DEFAULT_OUTPUT_DIR)
