"""
P0 tests: Loader data quality.

Covers: bundle_id fallback, developer NULL, platform normalization,
        date extraction, chart_type detection, duplicate import.
"""
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.loader import (
    parse_row, normalize_platform, extract_date_from_filename,
    extract_chart_type_from_filename, import_csv,
)
from src.storage.sqlite import get_db

# ── Helpers ─────────────────────────────────────────────────────

def _make_csv(filename: str, rows: list[dict]) -> str:
    """Write a temporary CSV and return the file path."""
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, filename)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    return path


passed = 0
failed = 0

def check(name: str, actual, expected, note: str = ""):
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        extra = f" ({note})" if note else ""
        print(f"  [FAIL] {name}: expected={repr(expected)}, got={repr(actual)}{extra}")


# ═══════════════════════════════════════════════════════════════
# 1. parse_row — data quality handling
# ═══════════════════════════════════════════════════════════════

print("── parse_row ──")

# bundle_id = "0" → fallback
row = parse_row(
    {"game_name": "翡翠经营模拟器", "bundle_id": "0", "platform_raw": "IOS游戏榜", "category": "游戏"},
    date="2026-06-16", chart_type="热门榜", source_file="test.csv"
)
check("bundle_id='0' fallback", row["bundle_id"], "fallback:翡翠经营模拟器")

# bundle_id empty → fallback
row = parse_row(
    {"game_name": "测试游戏", "bundle_id": "", "platform_raw": "IOS游戏榜", "category": "游戏"},
    date="2026-06-16", chart_type="热门榜", source_file="test.csv"
)
check("bundle_id empty fallback", row["bundle_id"], "fallback:测试游戏")

# developer = "0" → None
row = parse_row(
    {"game_name": "我的世界", "bundle_id": "com.netease.x19", "developer": "0", "platform_raw": "IOS游戏榜", "category": "游戏"},
    date="2026-06-16", chart_type="热门榜", source_file="test.csv"
)
check("developer='0' → None", row["developer"], None)

# developer empty → "" (kept as-is, only "0" is converted to None)
row = parse_row(
    {"game_name": "测试", "bundle_id": "com.test", "developer": "", "platform_raw": "IOS游戏榜", "category": "游戏"},
    date="2026-06-16", chart_type="热门榜", source_file="test.csv"
)
check("developer empty → ''", row["developer"], "")

# Normal bundle_id + developer
row = parse_row(
    {"game_name": "鸣潮", "bundle_id": "com.kurogame.mingchao", "developer": "库洛游戏", "platform_raw": "IOS游戏榜", "category": "游戏"},
    date="2026-06-16", chart_type="热门榜", source_file="test.csv"
)
check("normal bundle_id", row["bundle_id"], "com.kurogame.mingchao")
check("normal developer", row["developer"], "库洛游戏")


# ═══════════════════════════════════════════════════════════════
# 2. normalize_platform
# ═══════════════════════════════════════════════════════════════

print("\n── normalize_platform ──")

check("IOS游戏榜 → iOS", normalize_platform("IOS游戏榜"), "iOS")
check("IOS游戏榜 lowercase", normalize_platform("ios游戏榜"), "iOS")
check("ANDROID游戏榜 → Android", normalize_platform("ANDROID游戏榜"), "Android")
check("Android lowercase", normalize_platform("android游戏榜"), "Android")


# ═══════════════════════════════════════════════════════════════
# 3. extract_date_from_filename
# ═══════════════════════════════════════════════════════════════

print("\n── extract_date_from_filename ──")

check("hyphenated", extract_date_from_filename("2026-06-16_rankings.csv"), "2026-06-16")
check("hyphenated full path", extract_date_from_filename("/data/raw/2026-06-16_rankings.csv"), "2026-06-16")
check("compact 8-digit", extract_date_from_filename("TapTap_xxx_20260616.xlsx"), "2026-06-16")
check("compact 8-digit with path", extract_date_from_filename("data/raw/ios_20260616.csv"), "2026-06-16")
# Error case
try:
    extract_date_from_filename("no_date_here.csv")
    check("no date → raises", "no error", "ValueError")
except ValueError:
    check("no date → raises", "ValueError", "ValueError")


# ═══════════════════════════════════════════════════════════════
# 4. extract_chart_type_from_filename
# ═══════════════════════════════════════════════════════════════

print("\n── extract_chart_type_from_filename ──")

check("热门榜 explicit", extract_chart_type_from_filename("TapTap_Android_热门榜_20260616.xlsx"), "热门榜")
check("免费榜 explicit", extract_chart_type_from_filename("ios_免费榜_20260616.csv"), "免费榜")
check("畅销榜 explicit", extract_chart_type_from_filename("android_畅销榜_20260616.csv"), "畅销榜")
check("新品榜", extract_chart_type_from_filename("ios_新品榜_20260616.csv"), "新品榜")
check("下载榜", extract_chart_type_from_filename("ios_下载榜_20260616.csv"), "下载榜")
check("收入榜", extract_chart_type_from_filename("ios_收入榜_20260616.csv"), "收入榜")
check("default (no keyword)", extract_chart_type_from_filename("rankings.csv"), "热门榜")


# ═══════════════════════════════════════════════════════════════
# 5. CSV import → DB (integration)
# ═══════════════════════════════════════════════════════════════

print("\n── CSV import integration ──")

csv_path = _make_csv("ios_热门榜_20260617.csv", [
    {"排名": "1", "Bundle ID": "com.netease.x19", "应用": "我的世界", "iOS/Android": "IOS游戏榜", "类别": "游戏", "开发者": "0"},
    {"排名": "2", "Bundle ID": "com.kurogame.mingchao", "应用": "鸣潮", "iOS/Android": "IOS游戏榜", "类别": "游戏", "开发者": "库洛游戏"},
    {"排名": "3", "Bundle ID": "0", "应用": "翡翠经营模拟器", "iOS/Android": "IOS游戏榜", "类别": "游戏", "开发者": "0"},
])

n = import_csv(csv_path, date="2026-06-17")
check("import count", n.get("imported"), 3)

# Verify in DB
db = get_db()
rows = db.get_rankings_by_date("2026-06-17")
check("DB row count", len(rows), 3)

# Find the "0" bundle_id row → should be fallback
minecraft = [r for r in rows if r["game_name"] == "我的世界"][0]
check("DB: minecraft developer", minecraft["developer"], None)

jade = [r for r in rows if r["game_name"] == "翡翠经营模拟器"][0]
check("DB: jade bundle_id fallback", jade["bundle_id"], "fallback:翡翠经营模拟器")
check("DB: jade developer NULL", jade["developer"], None)

mingchao = [r for r in rows if r["game_name"] == "鸣潮"][0]
check("DB: mingchao bundle_id OK", mingchao["bundle_id"], "com.kurogame.mingchao")

# Re-import same data (should not duplicate)
n2 = import_csv(csv_path, date="2026-06-17")
check("re-import no error", n2.get("imported"), 3)
rows2 = db.get_rankings_by_date("2026-06-17")
check("re-import no duplicate", len(rows2), 3)

# Cleanup
db._connect().execute("DELETE FROM rankings WHERE date = '2026-06-17'")
db._connect().commit()
import shutil
shutil.rmtree(os.path.dirname(csv_path))

# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"  Loader: {passed} passed, {failed} failed")
print(f"{'='*50}")
if failed > 0:
    sys.exit(1)
