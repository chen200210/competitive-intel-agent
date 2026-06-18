"""
P0 tests: Story Picker rules.

Covers: 6 story types, dedup, priority sorting, MAX_STORIES=8 cap.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.story_picker import (
    pick_stories, detect_big_jumps, detect_black_horses, detect_cliff_drops,
    detect_cluster_moves, deduplicate_stories, story_priority, MAX_STORIES,
)

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


def _ch(**kw) -> dict:
    """Create a mock change with defaults."""
    defaults = {
        "game_name": "测试游戏", "bundle_id": "com.test", "developer": "测试工作室",
        "platform": "iOS", "chart_type": "热门榜", "category": "RPG",
        "today_rank": 50, "yesterday_rank": 50, "rank_change": 0,
        "change_type": "stable", "attention_score": 1.0, "is_significant": False,
    }
    defaults.update(kw)
    return defaults


# ═══════════════════════════════════════════════════════════════
# 1. Big Jump detection
# ═══════════════════════════════════════════════════════════════

print("── big_jump ──")

changes = [
    _ch(game_name="游戏A", change_type="up", rank_change=20, attention_score=8.0),
    _ch(game_name="游戏B", change_type="up", rank_change=15, attention_score=7.0),
    _ch(game_name="游戏C", change_type="up", rank_change=10, attention_score=5.0),
    _ch(game_name="游戏D", change_type="down", rank_change=25, attention_score=6.0),
]

stories = detect_big_jumps(changes)
check("count", len(stories), 2)  # only ≥15
check("A selected", stories[0]["game_name"], "游戏A")
check("B selected", stories[1]["game_name"], "游戏B")
check("story_type", stories[0]["story_type"], "big_jump")


# ═══════════════════════════════════════════════════════════════
# 2. Black Horse detection
# ═══════════════════════════════════════════════════════════════

print("\n── black_horse ──")

changes = [
    _ch(game_name="黑马A", change_type="new_entry", today_rank=7, yesterday_rank=None),
    _ch(game_name="黑马B", change_type="new_entry", today_rank=33, yesterday_rank=None),
    _ch(game_name="非黑马", change_type="new_entry", today_rank=55, yesterday_rank=None),
]

stories = detect_black_horses(changes)
check("count", len(stories), 2)  # A and B (≤50); 非黑马 excluded (>50)
check("A selected", stories[0]["game_name"], "黑马A")
check("B selected", stories[1]["game_name"], "黑马B")


# ═══════════════════════════════════════════════════════════════
# 3. Cliff Drop detection
# ═══════════════════════════════════════════════════════════════

print("\n── cliff_drop ──")

changes = [
    _ch(game_name="暴跌A", change_type="down", rank_change=-25, attention_score=7.0),
    _ch(game_name="掉榜B", change_type="dropped_out", today_rank=None, yesterday_rank=15),
    _ch(game_name="小幅跌", change_type="down", rank_change=-10, attention_score=4.0),
    _ch(game_name="尾掉榜", change_type="dropped_out", today_rank=None, yesterday_rank=55),
]

stories = detect_cliff_drops(changes)
check("count", len(stories), 2)  # 暴跌A (≥20) + 掉榜B (≤30)
check("A selected", stories[0]["game_name"], "暴跌A")
check("B selected", stories[1]["game_name"], "掉榜B")


# ═══════════════════════════════════════════════════════════════
# 4. Cluster Move detection
# ═══════════════════════════════════════════════════════════════

print("\n── cluster_move ──")

changes = [
    _ch(game_name="塔防A", category="塔防", change_type="up"),
    _ch(game_name="塔防B", category="塔防", change_type="up"),
    _ch(game_name="塔防C", category="塔防", change_type="up"),
    _ch(game_name="RPG_A", category="RPG", change_type="down"),
    _ch(game_name="RPG_B", category="RPG", change_type="down"),
    _ch(game_name="独狼", category="独游", change_type="up"),
]

stories = detect_cluster_moves(changes)
check(">=1 story", len(stories) >= 1, True)
# 塔防 3-ups should be detected
has_td = any("塔防" in str(s.get("story_headline", "")) for s in stories)
check("塔防 cluster found", has_td, True)


# ═══════════════════════════════════════════════════════════════
# 5. Dedup
# ═══════════════════════════════════════════════════════════════

print("\n── dedup ──")

stories = [
    {"story_type": "big_jump", "bundle_id": "com.a", "story_headline": "游戏A飙升"},
    {"story_type": "big_jump", "bundle_id": "com.a", "story_headline": "游戏A飙升"},  # duplicate
    {"story_type": "black_horse", "bundle_id": "com.b", "story_headline": "黑马B"},
]
deduped = deduplicate_stories(stories)
check("dedup count", len(deduped), 2)


# ═══════════════════════════════════════════════════════════════
# 6. Priority sorting
# ═══════════════════════════════════════════════════════════════

print("\n── priority sorting ──")

changes = [
    _ch(game_name="Cluster", category="塔防", change_type="up"),
    _ch(game_name="Cluster2", category="塔防", change_type="up"),
    _ch(game_name="Cluster3", category="塔防", change_type="up"),
    _ch(game_name="BigJump", change_type="up", rank_change=20, attention_score=7.0),
    _ch(game_name="BlackHorse", change_type="new_entry", today_rank=10, yesterday_rank=None),
    _ch(game_name="CliffDrop", change_type="down", rank_change=-25, attention_score=6.0),
]

result = pick_stories(changes)
check("big_jump first", result[0]["story_type"], "big_jump")
check("black_horse second", result[1]["story_type"], "black_horse")
check("cliff_drop third", result[2]["story_type"], "cliff_drop")


# ═══════════════════════════════════════════════════════════════
# 7. MAX_STORIES cap
# ═══════════════════════════════════════════════════════════════

print(f"\n── MAX_STORIES={MAX_STORIES} ──")

changes = []
for i in range(20):
    changes.append(_ch(
        game_name=f"BigJump{i}", bundle_id=f"com.big{i}",
        change_type="up", rank_change=20 + i, attention_score=8.0 - i * 0.3,
    ))

result = pick_stories(changes)
check(f"capped at {MAX_STORIES}", len(result) <= MAX_STORIES, True)


# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"  Story Picker: {passed} passed, {failed} failed")
print(f"{'='*50}")
if failed > 0:
    sys.exit(1)
