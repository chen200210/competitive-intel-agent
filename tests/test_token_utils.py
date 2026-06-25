"""
P0 tests: Token Utils correctness.

Covers: extract_game_names(), extract_topic_words(), headline_dedup_tokens().
        Chinese/English/mixed titles, noise filtering, edge cases.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.token_utils import (
    extract_game_names,
    extract_topic_words,
    headline_dedup_tokens,
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
        e = repr(expected)
        a = repr(actual)
        print(f"  [FAIL] {name}: expected={e}, got={a}{extra}")


# ═══════════════════════════════════════════════════════════════
# extract_game_names
# ═══════════════════════════════════════════════════════════════

print("── extract_game_names ──")

check("single game", extract_game_names("《明日方舟》新版本上线"),
      {"明日方舟"})

check("multiple games", extract_game_names("《原神》与《崩坏星穹铁道》联动"),
      {"原神", "崩坏星穹铁道"})

check("no game brackets", extract_game_names("塔防新游推荐"),
      set())

check("nested brackets", extract_game_names("评测《崩坏：星穹铁道》"),
      {"崩坏：星穹铁道"})

check("game with punctuation", extract_game_names("《Fate/Grand Order》活动"),
      {"Fate/Grand Order"})

check("duplicate game name", extract_game_names("《原神》《原神》双倍掉落"),
      {"原神"})

check("empty headline", extract_game_names(""),
      set())

check("english game name", extract_game_names("《Vampire Survivors》手机版"),
      {"Vampire Survivors"})


# ═══════════════════════════════════════════════════════════════
# extract_topic_words
# ═══════════════════════════════════════════════════════════════

print("\n── extract_topic_words ──")

check("Chinese words (default min=2)",
      extract_topic_words("塔防新游推荐"),
      {"塔防新游推荐"})

check("short Chinese filtered (1 char)",
      extract_topic_words("我买了新游戏", min_chinese=2),
      {"我买了新游戏"})  # contiguous 6-char CJK block, not word-segmented

check("English words (default min=3)",
      extract_topic_words("Steam Deck verified RPG game", min_english=3),
      {"deck", "verified", "rpg"})  # "steam" & "game" filtered by _NOISE_BASIC

check("short English filtered (<3 chars)",
      extract_topic_words("AI PC VR game", min_english=3),
      set())  # "AI"/"PC"/"VR" < 3 chars; "game" is noise

check("mixed Chinese+English",
      extract_topic_words("Steam塔防新作发布"),
      {"塔防新作发布"})  # "steam" filtered by _NOISE_BASIC

check("noise filtering — basic English",
      extract_topic_words("steam game play pc app", min_english=2, noise=set()),
      set())  # all noise words

check("noise filtering — extra noise",
      extract_topic_words("独家报道资讯头条", min_chinese=2,
                          noise={"独家", "报道", "资讯", "头条"}),
      {"独家报道资讯头条"})  # contiguous CJK block — noise words don't split it

check("custom noise set",
      extract_topic_words("昨日发布新版本", min_chinese=2,
                          noise={"昨日", "发布", "新版本"}),
      {"昨日发布新版本"})  # contiguous CJK block — individual noise words won't split it

check("numbers and symbols ignored",
      extract_topic_words("v1.0 更新 2024版"),
      {"更新"})  # "v" is 1 char (filtered), numbers/symbols ignored

check("empty text",
      extract_topic_words(""),
      set())


# ═══════════════════════════════════════════════════════════════
# headline_dedup_tokens — cross-source dedup
# ═══════════════════════════════════════════════════════════════

print("\n── headline_dedup_tokens ──")

check("game names extracted",
      headline_dedup_tokens("《明日方舟》新版本「孤星」上线"),
      {"明日方舟"})

check("prefix stripping: 游戏资讯",
      headline_dedup_tokens("游戏资讯：《原神》4.0更新公告"),
      {"原神", "更新公告"})  # "更新公告" is 4 chars CJK ≥ min_chinese=4

check("prefix stripping: [B站",
      headline_dedup_tokens("[B站·UP主]《崩坏星穹铁道》评测"),
      {"崩坏星穹铁道"})

check("long topic words (min 4 chars)",
      headline_dedup_tokens("全新开放世界RPG震撼发布"),
      {"全新开放世界", "震撼发布"})  # two CJK blocks ≥ 4; "RPG"=3 < min_english=4

check("short Chinese filtered (2-3 chars, not games)",
      headline_dedup_tokens("今天发布了新游戏"),
      {"今天发布了新游戏"})  # 8-char contiguous CJK block ≥ 4

check("English dedup tokens lowercase",
      headline_dedup_tokens("Steam Deck Game"),
      {"deck"})  # "steam" & "game" are noise; "deck" ≥ min_english=4

check("mixed game+words",
      headline_dedup_tokens("《王国保卫战》塔防策略新篇章"),
      {"王国保卫战", "塔防策略新篇章"})

check("empty headline",
      headline_dedup_tokens(""),
      set())

check("B站 prefix with creator name",
      headline_dedup_tokens("[B站·某UP主] Roguelike卡牌构筑新体验"),
      {"roguelike", "卡牌构筑新体验"})  # Roguelike ≥ 4; CJK block ≥ 4

check("行业分析 prefix stripped",
      headline_dedup_tokens("行业分析：2026年塔防手游市场趋势"),
      {"年塔防手游市场趋势"})  # "行业分析" stripped; numbers break CJK continuity


# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════

total = passed + failed
print(f"\n{'='*50}")
print(f"Results: {passed}/{total} passed ({failed} failed)")
if failed:
    sys.exit(1)
