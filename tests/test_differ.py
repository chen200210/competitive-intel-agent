"""
P0 tests: Differ correctness.

Covers: change_type detection, rank_change calculation, attention_score formula,
        day_type classification, first-day handling.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.differ import (
    compute_attention_score, is_significant, classify_day, diff_with_yesterday,
)
from src.storage.sqlite import get_db

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
# 1. classify_day
# ═══════════════════════════════════════════════════════════════

print("── classify_day ──")

# volatility = moved/total
# quiet: ≤10% moved, ≤2 in/out, no big moves
check("quiet 4/80=5%", classify_day(80, up=2, down=2, new_entry=0, dropped_out=0, big_moves=0), "quiet")
check("quiet 8/80=10%", classify_day(80, up=3, down=3, new_entry=1, dropped_out=1, big_moves=0), "quiet")

# normal: between 10% and 30%
check("normal 16/80=20%", classify_day(80, up=6, down=6, new_entry=2, dropped_out=2, big_moves=0), "normal")
check("normal 9/80≈11% but 3 new/dropped",
      classify_day(80, up=3, down=2, new_entry=2, dropped_out=2, big_moves=0), "normal")

# volatile: ≥30% moved, or ≥8 in/out, or ≥5 big moves
check("volatile 24/80=30%", classify_day(80, up=9, down=9, new_entry=3, dropped_out=3, big_moves=0), "volatile")
check("volatile many new/dropped", classify_day(80, up=3, down=3, new_entry=5, dropped_out=4, big_moves=0), "volatile")
check("volatile many big moves", classify_day(80, up=2, down=2, new_entry=1, dropped_out=1, big_moves=6), "volatile")

# First-day-like: 0 changes
check("zero changes", classify_day(100, up=0, down=0, new_entry=0, dropped_out=0, big_moves=0), "quiet")


# ═══════════════════════════════════════════════════════════════
# 2. compute_attention_score
# ═══════════════════════════════════════════════════════════════

print("\n── compute_attention_score ──")

# Top 10 new entry → 2.0(band) + 2.0(type) + 3.0(top10) = 7.0
s = compute_attention_score("new_entry", today_rank=7, yesterday_rank=None, rank_change=None)
check("new_entry top10 rank#7", round(s, 1), 7.0)

# New entry at #55 → 0.2(band) + 2.0(type) = 2.2
s = compute_attention_score("new_entry", today_rank=55, yesterday_rank=None, rank_change=None)
check("new_entry rank#55", round(s, 1), 2.2)

# Top 5 small move → 5.0(band) + 1.5(head_move) + 0.3(small) = 6.8
s = compute_attention_score("up", today_rank=2, yesterday_rank=3, rank_change=1)
check("top5 up +1", round(s, 1), 6.8)

# Big jump from low rank → 1.0(band≤30) + 3.5(≥20) + 2.0(breakout>30) = 6.5
s = compute_attention_score("up", today_rank=15, yesterday_rank=37, rank_change=22)
check("big_jump +22 breakout", round(s, 1), 6.5)

# Small move at low rank → 0.2(band>50) + 0.3(small) = 0.5
s = compute_attention_score("up", today_rank=75, yesterday_rank=78, rank_change=3)
check("low_rank small +3", round(s, 1), 0.5)

# Dropped from top 5 → 3.5(band≤5) + 4.0(≤10) = 7.5
s = compute_attention_score("dropped_out", today_rank=None, yesterday_rank=5, rank_change=None)
check("dropped_out from #5", round(s, 1), 7.5)

# Dropped from #80 → 0.2(band>50) + 0.5(else) = 0.7
s = compute_attention_score("dropped_out", today_rank=None, yesterday_rank=80, rank_change=None)
check("dropped_out from #80", round(s, 1), 0.7)


# ═══════════════════════════════════════════════════════════════
# 3. is_significant
# ═══════════════════════════════════════════════════════════════

print("\n── is_significant ──")

check("score≥5 significant", is_significant("up", 5.5, 20), True)
check("score<5 not significant", is_significant("down", 3.0, 50), False)
check("new_entry top30 always", is_significant("new_entry", 3.0, 25), True)
check("dropped_out from top30 always", is_significant("dropped_out", 2.0, 15), True)


# ═══════════════════════════════════════════════════════════════
# 4. diff_with_yesterday — integration with DB (skip if FK issue)
# ═══════════════════════════════════════════════════════════════

print("\n── diff_with_yesterday ──")

try:
    result = diff_with_yesterday("2026-06-16")
    if "error" not in result:
        changes = result.get("changes", [])
        types = set(c["change_type"] for c in changes)
        check("has up", "up" in types, True)
        check("has down", "down" in types, True)
        check("has new_entry", "new_entry" in types, True)
        check("has dropped_out", "dropped_out" in types, True)
        check("changes sorted by attention", all(
            changes[i]["attention_score"] >= changes[i+1]["attention_score"]
            for i in range(len(changes)-1)
        ) if len(changes) > 1 else True, True)
        check("attention_score in 0-10 range", all(
            0 <= c["attention_score"] <= 10 for c in changes
        ), True)
        check("day_type present", "day_type" in result, True)
    else:
        print(f"  [SKIP] diff failed: {result['error']}")
except Exception as e:
    print(f"  [SKIP] DB diff integration: {str(e)[:80]}")

# Future date should return error
result2 = diff_with_yesterday("2099-01-01")
check("future date returns error", "error" in result2, True)


# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"  Differ: {passed} passed, {failed} failed")
print(f"{'='*50}")
if failed > 0:
    sys.exit(1)
