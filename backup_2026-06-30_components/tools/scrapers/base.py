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

import httpx

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

    # Extra columns beyond STANDARD_COLUMNS that subclasses want to preserve
    EXTRA_COLUMNS: list[str] = []

    # HTTP timeout for httpx clients
    _timeout: float = 20.0

    # ── Column mapping ──
    # Subclasses define how their native column names map to internal field names.
    # Keys = scraper-native column names, Values = internal field name (rank / game_name / ...)
    #
    # Example: {"rank": "rank", "game": "game_name", "genre": "category", "publisher": "developer"}
    column_map: dict[str, str] = {}

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._client: httpx.Client | None = None

    # ── HTTP client ────────────────────────────────────────────

    def _get_client(self) -> httpx.Client:
        """Return a shared httpx.Client with standard gaming-site headers.

        Subclasses can override _timeout to adjust the request timeout.
        """
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
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
            )
        return self._client

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

        print(f"[{self.source_name or self.__class__.__name__}]")
        print(f"   Chart: {self.chart_type}  Platform: {self.platform}  Date: {date}")
        print(f"{'='*50}")

        # 1. Let the subclass collect data
        raw_rows = self.scrape()
        if not raw_rows:
            print("[WARN] No data collected")
            return None

        print(f"  Raw: {len(raw_rows)} rows")

        # 2. Normalize to standard format
        cleaned = self._clean(raw_rows, date)
        if not cleaned:
            print("[WARN] No valid rows after cleaning")
            return None

        print(f"  Cleaned: {len(cleaned)} rows")

        # 3. Write CSV
        csv_path = self._write_csv(cleaned, date or "")
        print(f"  Output: {csv_path}")
        print("[OK] Done!")

        return csv_path

    def scrape(self) -> list[dict[str, Any]]:
        """
        Collect raw ranking data. Override in subclass.

        Returns a list of dicts with scraper-native column names.
        Each dict is one game's ranking row.
        """
        raise NotImplementedError("Subclasses must implement scrape()")

    # ── Internal: normalization ─────────────────────────────────

    def _clean(self, raw_rows: list[dict[str, Any]], date: str = "") -> list[dict[str, str]]:
        """Map scraper-native columns → standard columns, inject metadata.

        The date parameter is accepted for compatibility with subclasses that
        may override this method (e.g. bilibili_creators). The base implementation
        does not use it — the date is applied at CSV write time via _write_csv().
        """
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
            bundle_id = mapped.get("bundle_id", "").strip()
            if bundle_id in ("0", "0.0", "") or not bundle_id:
                bundle_id = f"fallback:{game_name}"

            # Rank
            rank_str = mapped.get("rank", "0")
            try:
                rank = int(rank_str)
            except (ValueError, TypeError):
                rank = 0

            clean_row = {
                "排名": str(rank),
                "Bundle ID": bundle_id,
                "应用": game_name,
                "平台": self.platform,
                "榜单": self.chart_type,
                "品类": mapped.get("category", ""),
                "开发者": mapped.get("developer", ""),
            }
            # Merge EXTRA_COLUMNS — always write every key (even empty)
            # so all rows have structurally identical columns.
            for col in self.EXTRA_COLUMNS:
                val = row.get(col, "")
                clean_row[col] = str(val) if val is not None else ""
            cleaned.append(clean_row)

        # Remove columns that are entirely empty (including EXTRA_COLUMNS)
        if cleaned:
            populated_cols = [
                col for col in list(STANDARD_COLUMNS) + list(self.EXTRA_COLUMNS)
                if any(r.get(col, "") != "" for r in cleaned)
            ]
            cleaned = [{k: r.get(k, "") for k in populated_cols} for r in cleaned]

        return cleaned

    # ── Internal: CSV output ────────────────────────────────────

    def _write_csv(self, rows: list[dict[str, str]], date: str = "") -> Path:
        """Write cleaned rows to a CSV file with standard naming."""
        date_str = (date or datetime.now().strftime("%Y%m%d")).replace("-", "")

        # Sanitize platform and chart_type for filenames
        platform_slug = self.platform.lower().replace(" ", "_").replace("/", "_")
        chart_slug = self.chart_type

        filename = f"{platform_slug}_{chart_slug}_{date_str}.csv"
        csv_path = self.output_dir / filename

        fieldnames = list(rows[0].keys()) if rows else []

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
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
