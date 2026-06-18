"""
Story Picker — pure rule engine that selects up to 8 candidate stories from 20–40 changes.

Zero AI token consumption. Runs after Differ, before any Agent.

The cap is 8 (relaxed from 5). The Overview Scanner agent makes the final cut
of 5–8 stories based on industry context and story quality.

Six story types (per DESIGN.md §5.3.5 + §7.6.5):
  1. 🔺 Big Jump          — rank up ≥ 15 positions
  2. 🆕 Black Horse       — new entry into top 50
  3. 📉 Cliff Drop        — rank down ≥ 20, or dropped from top 30
  4. 📐 Cross-Chart Signal — multi-chart pattern detected (leading/traffic_leak/harvest/word_of_mouth/divergence)
  5. 📈 Steady Climber    — 5+ consecutive days of rank improvement
  6. 🎯 Cluster Move      — ≥3 games from same genre/developer moving together

Usage:
    python -m src.pipeline.story_picker --date 2026-06-16
"""

from collections import defaultdict
from typing import Any

from src.storage.sqlite import get_db


# ── Story Type Configuration ─────────────────────────────────

STORY_TYPES = {
    "big_jump": {
        "label": "🔺 大幅跃升",
        "priority": 100,
        "description": "排名上升 ≥ 15 位",
    },
    "black_horse": {
        "label": "🆕 黑马突围",
        "priority": 95,
        "description": "新上榜直接进入前 50",
    },
    "cliff_drop": {
        "label": "📉 断崖下跌",
        "priority": 90,
        "description": "排名下跌 ≥ 20 位或从前 30 掉榜",
    },
    "cross_chart_signal": {
        "label": "📐 跨榜信号",
        "priority": 85,
        "description": "跨榜单对照检测到的异常信号（全面领跑/流量型/收割型/口碑型/背离）",
    },
    "steady_climber": {
        "label": "📈 持续爬升",
        "priority": 80,
        "description": "连续 5+ 天排名上升",
    },
    "cluster_move": {
        "label": "🎯 品类异动",
        "priority": 70,
        "description": "同一品类/开发商 ≥ 3 款游戏同向变动",
    },
}

# Maximum candidate stories to output (Agent makes final 5–8 cut)
MAX_STORIES = 8


# ── Story Detection Functions ────────────────────────────────

def detect_big_jumps(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Type 1: Rank up ≥ 15 positions."""
    stories: list[dict[str, Any]] = []
    for c in changes:
        if c["change_type"] == "up" and c["rank_change"] is not None and c["rank_change"] >= 15:
            stories.append({
                **c,
                "story_type": "big_jump",
                "story_headline": f"{c['game_name']} 排名飙升 {c['rank_change']} 位",
                "story_angle": "是什么驱动了这次跃升？版本更新？活动？还是突然的自然增长？",
            })
    return stories


def detect_black_horses(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Type 2: New entry directly into top 50."""
    stories: list[dict[str, Any]] = []
    for c in changes:
        if (c["change_type"] == "new_entry"
                and c["today_rank"] is not None
                and c["today_rank"] <= 50):
            stories.append({
                **c,
                "story_type": "black_horse",
                "story_headline": f"黑马「{c['game_name']}」首次上榜即进入第 {c['today_rank']} 位",
                "story_angle": "这款游戏是谁？什么玩法？怎么突然冲上来的？",
            })
    return stories


def detect_cliff_drops(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Type 3: Rank down ≥ 20, or dropped out from top 30."""
    stories: list[dict[str, Any]] = []
    for c in changes:
        is_cliff = (
            (c["change_type"] == "down"
             and c["rank_change"] is not None
             and abs(c["rank_change"]) >= 20)
            or (c["change_type"] == "dropped_out"
                and c["yesterday_rank"] is not None
                and c["yesterday_rank"] <= 30)
        )
        if is_cliff:
            headline = (
                f"{c['game_name']} 排名暴跌 {abs(c['rank_change'])} 位"
                if c["change_type"] == "down"
                else f"{c['game_name']} 从前 {c['yesterday_rank']} 位掉榜"
            )
            stories.append({
                **c,
                "story_type": "cliff_drop",
                "story_headline": headline,
                "story_angle": "出了什么问题？Bug？舆情危机？还是竞品挤压？",
            })
    return stories


def detect_steady_climbers(
    changes: list[dict[str, Any]],
    min_days: int = 5,
) -> list[dict[str, Any]]:
    """
    Type 4: Games with 5+ consecutive days of rank improvement.

    Checks the database for each game that went up today to see if
    it has been rising for at least `min_days` consecutive days.
    """
    stories: list[dict[str, Any]] = []
    db = get_db()

    for c in changes:
        if c["change_type"] != "up":
            continue
        bundle_id = c["bundle_id"]
        history = db.get_game_history(bundle_id, days=30)

        if len(history) < min_days:
            continue

        # Count consecutive days of rank improvement (lower rank number = better)
        streak = 1
        for i in range(len(history) - 1, 0, -1):
            curr = history[i]
            prev = history[i - 1]
            if curr["rank"] < prev["rank"]:  # improved (lower number)
                streak += 1
            else:
                break

        if streak >= min_days:
            start_rank = history[-streak]["rank"] if len(history) >= streak else history[0]["rank"]
            stories.append({
                **c,
                "story_type": "steady_climber",
                "story_headline": (
                    f"「{c['game_name']}」连续 {streak} 天上升，"
                    f"从第 {start_rank} 位到第 {c['today_rank']} 位"
                ),
                "story_angle": "不是偶然的波动——持续爬升意味着什么？增长动力是什么？",
                "streak_days": streak,
                "start_rank": start_rank,
            })

    return stories


def detect_cluster_moves(
    changes: list[dict[str, Any]],
    min_games: int = 3,
) -> list[dict[str, Any]]:
    """
    Type 5: ≥3 games from the same category or developer moving in the same direction.

    Groups changes by category and by developer, flags clusters.
    """
    stories: list[dict[str, Any]] = []

    # Group by category + direction
    cat_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in changes:
        if c["change_type"] in ("up", "down"):
            key = (c.get("category", "unknown"), c["change_type"])
            cat_groups[key].append(c)

    for (category, direction), group in cat_groups.items():
        if len(group) >= min_games:
            dir_label = "集体上升" if direction == "up" else "集体下跌"
            stories.append({
                "story_type": "cluster_move",
                "story_headline": f"{category}品类 {len(group)} 款游戏{dir_label}",
                "story_angle": "不是单个游戏的事——整个品类在变化。原因是什么？",
                "category": category,
                "direction": dir_label,
                "games": [g["game_name"] for g in group],
            })

    # Group by developer + direction
    dev_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in changes:
        dev = c.get("developer") or "unknown"
        if c["change_type"] in ("up", "down"):
            key = (dev, c["change_type"])
            dev_groups[key].append(c)

    for (developer, direction), group in dev_groups.items():
        if len(group) >= min_games and developer != "unknown":
            dir_label = "集体上升" if direction == "up" else "集体下跌"
            stories.append({
                "story_type": "cluster_move",
                "story_headline": f"「{developer}」旗下 {len(group)} 款游戏{dir_label}",
                "story_angle": "同一开发商多款产品同步变动——是公司层面的策略调整？",
                "developer": developer,
                "direction": dir_label,
                "games": [g["game_name"] for g in group],
            })

    return stories


# ── Dedup & Sort ─────────────────────────────────────────────

def _story_key(story: dict[str, Any]) -> str:
    """Generate a dedup key for a story."""
    return f"{story.get('story_type')}:{story.get('bundle_id', story.get('story_headline', ''))}"


def deduplicate_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate stories (same type + same game)."""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for s in stories:
        key = _story_key(s)
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


def story_priority(story: dict[str, Any]) -> float:
    """Compute sort priority for a story. Higher = more important."""
    base = STORY_TYPES.get(story.get("story_type", ""), {}).get("priority", 0)
    # Within same type, use attention_score as tiebreaker
    tiebreaker = story.get("attention_score", 0)
    return base + tiebreaker * 0.1


# ── Main Entry Point ─────────────────────────────────────────

def pick_stories(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Select up to MAX_STORIES stories from the full change list.

    Pure rule engine — zero AI cost.

    Args:
        changes: List of change dicts from Differ.

    Returns:
        Sorted list of up to 5 story dicts.
    """
    all_stories: list[dict[str, Any]] = []

    # Type 1: Big Jumps
    all_stories.extend(detect_big_jumps(changes))

    # Type 2: Black Horses
    all_stories.extend(detect_black_horses(changes))

    # Type 3: Cliff Drops
    all_stories.extend(detect_cliff_drops(changes))

    # Type 4: Steady Climbers (requires DB history)
    all_stories.extend(detect_steady_climbers(changes))

    # Type 5: Cluster Moves
    all_stories.extend(detect_cluster_moves(changes))

    # Dedup and sort
    all_stories = deduplicate_stories(all_stories)
    all_stories.sort(key=story_priority, reverse=True)

    return all_stories[:MAX_STORIES]


def pick_stories_for_date(date: str) -> dict[str, Any]:
    """Full pipeline: read changes from DB, pick stories, merge cross-chart signals, return summary.

    Returns up to 8 candidates. The Overview Scanner agent makes the final 5–8 cut
    based on industry context.
    """
    db = get_db()
    changes = db.get_changes_by_date(date)

    # 1. Single-chart stories from change records
    stories = pick_stories(changes) if changes else []

    # 2. Cross-chart stories (multi-chart signal patterns)
    try:
        from src.pipeline.cross_chart import cross_chart_stories
        cross_stories = cross_chart_stories(date)
        if cross_stories:
            # Merge: cross-chart stories compete with single-chart stories
            all_candidates = stories + cross_stories
            # Re-sort by priority
            all_candidates.sort(key=story_priority, reverse=True)
            # De-duplicate (cross_chart may detect the same game as a single-chart story)
            all_candidates = deduplicate_stories(all_candidates)
            stories = all_candidates[:MAX_STORIES]
    except ImportError:
        pass  # cross_chart module not available yet

    return {
        "date": date,
        "total_changes": len(changes) if changes else 0,
        "stories_selected": len(stories),
        "stories": stories,
    }


# ── CLI test entry ───────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json

    date_arg = sys.argv[2] if len(sys.argv) >= 4 and sys.argv[1] == "--date" else None
    if date_arg is None:
        db = get_db()
        dates = db.get_available_dates()
        if not dates:
            print("No data in database. Import a CSV first.")
            sys.exit(1)
        date_arg = dates[0]

    result = pick_stories_for_date(date_arg)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
