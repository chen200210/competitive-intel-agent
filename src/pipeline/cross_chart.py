"""
Cross-Chart Comparison Analysis — detect multi-chart signal patterns.

Pure computation, zero AI cost. Runs after Differ, before Story Picker.

Five signal patterns (per DESIGN.md §7.6.2):
  1. leading        — all charts strong (top 15): product in full breakout
  2. traffic_leak   — free/download chart strong, grossing chart weak: good acquisition, poor monetization
  3. harvest        — grossing chart strong, free chart weak: small base, high ARPU
  4. word_of_mouth  — hot chart significantly stronger than free chart: community-driven growth
  5. divergence     — significant mismatch between any two charts

Each signal gets a threat_level: high / medium / low.

Usage:
    python -m src.pipeline.cross_chart --date 2026-06-16
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from src.storage.sqlite import get_db


# ── Configuration ───────────────────────────────────────────────

# "Strong" rank threshold — top N in a chart is clearly strong
STRONG_RANK = 15

# Minimum delta between two chart ranks to be considered a significant signal.
# e.g., 免费榜 #5 vs 畅销榜 #48 → delta 43 → signal-worthy mismatch.
SIGNIFICANT_DELTA = 25

# Chart category mapping — groups similar chart types for comparison
# "acquisition" charts measure downloads / user growth
# "monetization" charts measure revenue / IAP
# "engagement" charts measure community / popularity
CHART_FAMILIES: dict[str, str] = {
    "免费榜": "acquisition",
    "下载榜": "acquisition",
    "畅销榜": "monetization",
    "收入榜": "monetization",
    "热门榜": "engagement",
    "新品榜": "acquisition",  # new releases ≈ acquisition
}


# ── Pattern Detection ──────────────────────────────────────────

def _get_best_rank(charts: dict[str, int], family: str) -> int | None:
    """Get the best (lowest) rank for a chart family.

    e.g., if a game appears in both 免费榜 #8 and 下载榜 #12,
    the best acquisition rank is 8.
    """
    ranks = [
        rank for chart, rank in charts.items()
        if CHART_FAMILIES.get(chart) == family
    ]
    return min(ranks) if ranks else None


def detect_signal(
    bundle_id: str,
    game_name: str,
    charts: dict[str, int],
) -> dict[str, Any] | None:
    """
    Detect the cross-chart signal pattern for a single game.

    Args:
        bundle_id: App store bundle ID.
        game_name: Human-readable game name.
        charts: Dict of chart_type → rank, e.g. {"免费榜": 5, "畅销榜": 48}.

    Returns:
        Signal dict if a pattern is detected, None otherwise.
        Signal dict keys: bundle_id, game_name, charts_json, signal_pattern,
                          signal_description, threat_level.
    """
    if len(charts) < 2:
        return None  # Can't compare with only one chart

    acq_rank = _get_best_rank(charts, "acquisition")
    mon_rank = _get_best_rank(charts, "monetization")
    eng_rank = _get_best_rank(charts, "engagement")

    all_ranks = list(charts.values())

    # ── Pattern 1: Leading (全面领跑) ──
    # All available charts show strong positions (top STRONG_RANK).
    if all(r <= STRONG_RANK for r in all_ranks) and len(charts) >= 2:
        chart_summary = "、".join(f"{c}#{r}" for c, r in sorted(charts.items()))
        return {
            "bundle_id": bundle_id,
            "game_name": game_name,
            "charts_json": json.dumps(charts, ensure_ascii=False),
            "signal_pattern": "leading",
            "signal_description": f"全面领跑——各榜排名均靠前（{chart_summary}），产品处于全面爆发期",
            "threat_level": "high",
        }

    # ── Pattern 2: Traffic Leak (流量型) ──
    # Acquisition strong (top STRONG_RANK), monetization significantly worse.
    if acq_rank is not None and mon_rank is not None:
        if acq_rank <= STRONG_RANK and (mon_rank - acq_rank) >= SIGNIFICANT_DELTA:
            return {
                "bundle_id": bundle_id,
                "game_name": game_name,
                "charts_json": json.dumps(charts, ensure_ascii=False),
                "signal_pattern": "traffic_leak",
                "signal_description": (
                    f"流量型——获客能力强（免费/下载榜#{acq_rank}）但变现能力滞后"
                    f"（畅销/收入榜#{mon_rank}），高下载低付费的漏斗问题"
                ),
                "threat_level": "medium",
            }

    # ── Pattern 3: Harvest (收割型) ──
    # Monetization strong (top STRONG_RANK), acquisition significantly worse.
    if acq_rank is not None and mon_rank is not None:
        if mon_rank <= STRONG_RANK and (acq_rank - mon_rank) >= SIGNIFICANT_DELTA:
            return {
                "bundle_id": bundle_id,
                "game_name": game_name,
                "charts_json": json.dumps(charts, ensure_ascii=False),
                "signal_pattern": "harvest",
                "signal_description": (
                    f"收割型——商业化能力强（畅销/收入榜#{mon_rank}）但获客面窄"
                    f"（免费/下载榜#{acq_rank}），小圈子高付费，破不了圈"
                ),
                "threat_level": "medium",
            }

    # ── Pattern 4: Word of Mouth (口碑型) ──
    # Engagement chart significantly stronger than acquisition chart.
    if acq_rank is not None and eng_rank is not None:
        if eng_rank <= acq_rank - SIGNIFICANT_DELTA:
            return {
                "bundle_id": bundle_id,
                "game_name": game_name,
                "charts_json": json.dumps(charts, ensure_ascii=False),
                "signal_pattern": "word_of_mouth",
                "signal_description": (
                    f"口碑型——社区热度领先（热门榜#{eng_rank}）远超获客排名"
                    f"（免费/下载榜#{acq_rank}），玩家自来水驱动，是下载量增长的领先指标"
                ),
                "threat_level": "medium",
            }

    # ── Pattern 5: Divergence (信号背离) ──
    # Any significant mismatch between two chart families.
    if acq_rank is not None and mon_rank is not None:
        delta = abs(acq_rank - mon_rank)
        if delta >= SIGNIFICANT_DELTA:
            if acq_rank < mon_rank:
                direction = "买量催出来的虚假繁荣——获客强但变现完全跟不上"
            else:
                direction = "老游戏靠活动续命——付费还在但新用户进不来"
            return {
                "bundle_id": bundle_id,
                "game_name": game_name,
                "charts_json": json.dumps(charts, ensure_ascii=False),
                "signal_pattern": "divergence",
                "signal_description": (
                    f"信号背离——免费/下载榜#{acq_rank} vs 畅销/收入榜#{mon_rank}，"
                    f"排名差距{delta}位。{direction}"
                ),
                "threat_level": "medium",
            }

    # Check for other divergence patterns
    if acq_rank is not None and eng_rank is not None:
        delta = abs(acq_rank - eng_rank)
        if delta >= SIGNIFICANT_DELTA and delta > abs((acq_rank or 99) - (mon_rank or 99)):
            # Only report if this is the most notable divergence
            return {
                "bundle_id": bundle_id,
                "game_name": game_name,
                "charts_json": json.dumps(charts, ensure_ascii=False),
                "signal_pattern": "divergence",
                "signal_description": (
                    f"信号背离——获客排名#{acq_rank} vs 社区热度#{eng_rank}，"
                    f"排名差距{delta}位。平台策略或渠道分布可能有异常"
                ),
                "threat_level": "low",
            }

    return None  # No clear signal detected


# ── Main Entry Point ────────────────────────────────────────────

def analyze_cross_chart(date: str) -> dict[str, Any]:
    """
    Run cross-chart analysis for a given date.

    Loads all rankings for the date, groups by bundle_id to create
    multi-chart profiles, detects signal patterns, and writes results
    to the cross_chart_signals table.

    Args:
        date: Date string in 'YYYY-MM-DD' format.

    Returns:
        Summary dict with date, chart_types_found, games_analyzed,
        signals_found, and per-signal breakdown.
    """
    db = get_db()

    # 1. Load all rankings for this date
    rankings = db.get_rankings_by_date(date)
    if not rankings:
        return {
            "date": date,
            "error": f"No ranking data found for {date}",
            "signals": [],
        }

    # 2. Discover available chart types
    chart_types = sorted(set(r["chart_type"] for r in rankings))
    if len(chart_types) < 2:
        return {
            "date": date,
            "chart_types_found": chart_types,
            "hint": f"Only {len(chart_types)} chart type(s) found — need ≥2 for cross-chart analysis. Import data from multiple charts (e.g., 免费榜 + 畅销榜 + 热门榜).",
            "games_analyzed": len(rankings),
            "signals_found": 0,
            "signals": [],
        }

    # 3. Pivot: bundle_id → {chart_type: rank}
    #    If a game appears multiple times in the same chart_type (shouldn't happen
    #    due to UNIQUE constraint), take the best rank.
    game_charts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"charts": {}, "game_name": "", "developer": ""}
    )
    for r in rankings:
        bid = r["bundle_id"]
        ct = r["chart_type"]
        rank = r["rank"]

        game_charts[bid]["game_name"] = r["game_name"]
        game_charts[bid]["developer"] = r.get("developer") or ""
        # Keep the best rank if duplicate (shouldn't happen, but safe)
        if ct not in game_charts[bid]["charts"] or rank < game_charts[bid]["charts"][ct]:
            game_charts[bid]["charts"][ct] = rank

    # 4. Detect signals for each game appearing in ≥2 charts
    signals: list[dict[str, Any]] = []
    games_analyzed = 0

    for bundle_id, info in game_charts.items():
        charts = info["charts"]
        if len(charts) < 2:
            continue  # Skip single-chart games

        games_analyzed += 1
        signal = detect_signal(
            bundle_id=bundle_id,
            game_name=info["game_name"],
            charts=charts,
        )
        if signal is not None:
            signal["date"] = date
            signals.append(signal)

    # 5. Sort: high threat first, then by pattern type
    threat_order = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda s: (threat_order.get(s["threat_level"], 3), s["signal_pattern"]))

    # 6. Persist to database
    if signals:
        db.insert_cross_chart_signals(signals)

    # 7. Build summary
    pattern_counts: dict[str, int] = defaultdict(int)
    for s in signals:
        pattern_counts[s["signal_pattern"]] += 1

    return {
        "date": date,
        "chart_types_found": chart_types,
        "games_analyzed": games_analyzed,
        "multi_chart_games": games_analyzed,
        "signals_found": len(signals),
        "by_pattern": dict(pattern_counts),
        "by_threat": {
            "high": sum(1 for s in signals if s["threat_level"] == "high"),
            "medium": sum(1 for s in signals if s["threat_level"] == "medium"),
            "low": sum(1 for s in signals if s["threat_level"] == "low"),
        },
        "signals": signals,
    }


def get_signals_for_date(date: str) -> list[dict[str, Any]]:
    """Return cross-chart signals for a date (reads from DB, doesn't recompute)."""
    db = get_db()
    return db.get_cross_chart_signals(date=date)


def get_high_threat_signals(date: str | None = None) -> list[dict[str, Any]]:
    """Return high-threat cross-chart signals, optionally filtered by date."""
    db = get_db()
    return db.get_cross_chart_signals(date=date, threat_level="high")


# ── Story Picker Integration ────────────────────────────────────

def cross_chart_stories(date: str) -> list[dict[str, Any]]:
    """
    Generate Story Picker-compatible story entries from cross-chart signals.

    These stories can be fed into Story Picker alongside single-chart stories
    (big_jump, black_horse, etc.) and compete for the 5 daily story slots.

    Returns:
        List of story dicts with story_type="cross_chart_signal".
    """
    signals = get_signals_for_date(date)
    if not signals:
        # Try computing fresh
        result = analyze_cross_chart(date)
        signals = result.get("signals", [])

    stories: list[dict[str, Any]] = []
    for sig in signals:
        charts = json.loads(sig["charts_json"]) if isinstance(sig["charts_json"], str) else sig["charts_json"]

        # Map signal pattern to story angle
        story_angles = {
            "leading": "为什么这款游戏能在所有榜单全面领跑？做对了什么？",
            "traffic_leak": "高下载低付费的漏斗问题——是商业化设计缺陷还是产品阶段所致？",
            "harvest": "小众高付费群体的需求特征是什么？这个付费模型可持续吗？",
            "word_of_mouth": "社区热度能否转化为下载增长？什么内容在驱动口碑传播？",
            "divergence": "两榜背离的原因是什么？是结构性变化还是暂时性波动？",
        }

        chart_summary = "、".join(f"{c}#{r}" for c, r in sorted(charts.items()))
        stories.append({
            "story_type": "cross_chart_signal",
            "story_headline": f"「{sig['game_name']}」{chart_summary} — {sig['signal_description'][:50]}",
            "game_name": sig["game_name"],
            "bundle_id": sig["bundle_id"],
            "signal_pattern": sig["signal_pattern"],
            "charts": charts,
            "story_angle": story_angles.get(sig["signal_pattern"], "跨榜信号值得深入分析"),
            "threat_level": sig["threat_level"],
            "signal_description": sig["signal_description"],
        })

    # Sort: high threat → leading → divergence → others
    threat_order = {"high": 0, "medium": 1, "low": 2}
    pattern_order = {"leading": 0, "divergence": 1, "traffic_leak": 2, "harvest": 3, "word_of_mouth": 4}
    stories.sort(key=lambda s: (
        threat_order.get(s["threat_level"], 3),
        pattern_order.get(s["signal_pattern"], 5),
    ))

    return stories


# ── CLI test entry ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    date_arg = sys.argv[2] if len(sys.argv) >= 4 and sys.argv[1] == "--date" else None
    if date_arg is None:
        db = get_db()
        dates = db.get_available_dates()
        if not dates:
            print("No data in database. Import a CSV first.")
            sys.exit(1)
        date_arg = dates[0]

    result = analyze_cross_chart(date_arg)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
