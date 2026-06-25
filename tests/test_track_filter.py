"""
P0 tests: Track Filter correctness.

Covers: classify_game() for all 5 keyword categories, Steam port rule,
        track-overrides-ignored priority, brand signals, edge cases.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.track_filter import classify_game

passed = 0
failed = 0


def check(name: str, expected: str, **kwargs):
    global passed, failed
    result = classify_game(**kwargs)
    if result == expected:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}: expected={expected}, got={result}")


# ═══════════════════════════════════════════════════════════════
# 1. Track keyword matches
# ═══════════════════════════════════════════════════════════════

print("── Track keyword matches ──")

check("塔防 in genre", "track",
      game_name="暗夜防线", genre="塔防")

check("塔防 in tags", "track",
      game_name="边境守护", tags=["策略", "塔防"])

check("肉鸽 in genre", "track",
      game_name="地牢探险", genre="肉鸽")

check("Roguelike EN", "track",
      game_name="Dungeon Crawl", genre="Roguelike")

check("Roguelite EN", "track",
      game_name="Hades Clone", tags=["Roguelite", "动作"])

check("TD abbreviation", "track",
      game_name="Element TD", genre="TD")

check("Tower Defense full", "track",
      game_name="Blons TD", genre="Tower Defense")

check("割草 (Vampire Survivors-like)", "track",
      game_name="灵魂收割", genre="割草")

check("Vampire Survivors name match", "track",
      game_name="某幸存者游戏", description="Vampire Survivors like玩法")

check("割草 in description", "track",
      game_name="新游戏", description="这是一款割草游戏")


# ═══════════════════════════════════════════════════════════════
# 2. Track overrides ignored (priority rule)
# ═══════════════════════════════════════════════════════════════

print("\n── Track overrides ignored ──")

check("明日方舟: 塔防+二次元 → track",
      "track", game_name="明日方舟", genre="塔防", tags=["塔防", "二次元"])

check("肉鸽+女性向 → track",
      "track", game_name="某肉鸽乙女", genre="Roguelike", tags=["肉鸽", "女性向"])

check("TD+乙女 in description → track",
      "track", game_name="某游戏", description="Tower Defense 乙女向新作")

check("割草+二次元 → track",
      "track", game_name="二次元割草", genre="割草", tags=["二次元"])


# ═══════════════════════════════════════════════════════════════
# 3. Ignored only (no track keyword present)
# ═══════════════════════════════════════════════════════════════

print("\n── Ignored category matches ──")

check("女性向 pure", "ignored",
      game_name="恋爱养成", genre="女性向")

check("二次元 pure", "ignored",
      game_name="某二次元RPG", genre="RPG", tags=["二次元"])

check("乙女 pure", "ignored",
      game_name="乙女向恋爱", genre="乙女", tags=["恋爱", "卡牌"])

check("女性向 in tags", "ignored",
      game_name="换装游戏", tags=["换装", "女性向"])


# ═══════════════════════════════════════════════════════════════
# 4. Ignored brand signals (well-known games by name)
# ═══════════════════════════════════════════════════════════════

print("\n── Ignored brand signals ──")

check("恋与深空 by name", "ignored",
      game_name="恋与深空", description="第六男主狼人PV公布")

check("恋与制作人 by name only", "ignored",
      game_name="恋与制作人")

check("光与夜之恋 by name", "ignored",
      game_name="光与夜之恋", tags=["恋爱", "卡牌"])

check("闪耀暖暖 by name", "ignored",
      game_name="闪耀暖暖", tags=["换装", "3D"])

check("奇迹暖暖 by name", "ignored",
      game_name="奇迹暖暖")

check("无限暖暖 by name", "ignored",
      game_name="无限暖暖", genre="开放世界")

check("未定事件簿 by name", "ignored",
      game_name="未定事件簿")

check("时空中的绘旅人 by name", "ignored",
      game_name="时空中的绘旅人")

check("世界之外 by name", "ignored",
      game_name="世界之外")

check("恋与 prefix catch-all", "ignored",
      game_name="恋与江湖")


# ═══════════════════════════════════════════════════════════════
# 5. Neutral (neither track nor ignored)
# ═══════════════════════════════════════════════════════════════

print("\n── Neutral ──")

check("MOBA", "neutral",
      game_name="王者荣耀", genre="MOBA", tags=["竞技", "5v5"])

check("FPS", "neutral",
      game_name="和平精英", genre="FPS", tags=["射击"])

check("RPG", "neutral",
      game_name="原神", genre="RPG", tags=["开放世界"])

check("no tags at all", "neutral",
      game_name="未知游戏")

check("sports game", "neutral",
      game_name="FIFA Mobile", genre="体育", tags=["足球"])


# ═══════════════════════════════════════════════════════════════
# 6. Steam port special rule (always track)
# ═══════════════════════════════════════════════════════════════

print("\n── Steam port rule ──")

check("Steam port no track match → track",
      "track", game_name="某PC移植游戏", genre="RPG", is_steam_port=True)

check("Steam port + anime → track (steam wins)",
      "track", game_name="某PC移植二次元", genre="RPG",
      tags=["二次元"], is_steam_port=True)

check("Steam port + no keywords at all → track",
      "track", game_name="Steam游戏", is_steam_port=True)


# ═══════════════════════════════════════════════════════════════
# 7. Edge cases
# ═══════════════════════════════════════════════════════════════

print("\n── Edge cases ──")

check("empty game name", "neutral", game_name="")

check("keyword in developer name", "track",
      game_name="某游戏", developer="塔防工作室")

check("keyword in description (not genre/tags)", "track",
      game_name="无名新游", description="融合了Roguelike元素的卡牌游戏")

check("Chinese + English mix — 肉鸽 in description", "track",
      game_name="Card Quest", description="一款肉鸽卡牌游戏")

check("case insensitive EN", "track",
      game_name="Dungeon Game", genre="roguelike")

check("case insensitive CN (no-op for Chinese)", "track",
      game_name="塔防守卫", genre="策略")


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════

total = passed + failed
print(f"\n{'='*50}")
print(f"Results: {passed}/{total} passed ({failed} failed)")
if failed:
    sys.exit(1)
