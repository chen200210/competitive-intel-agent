"""
Differ — compare today's rankings against yesterday's to produce change records.

Each change gets an attention_score (0–10) and is_significant flag.
The day is classified as quiet / normal / volatile.

Algorithm per DESIGN.md §5.3.

Usage:
    python -m src.pipeline.differ --date 2026-06-16
"""

from typing import Any

import sys

from src.storage.sqlite import get_db
from src.types import RankingEntry, ChangeRecord


# ── Attention Score ──────────────────────────────────────────

def compute_attention_score(
    change_type: str,
    today_rank: int | None,
    yesterday_rank: int | None,
    rank_change: int | None,
    game_name: str = "",
) -> float:
    """
    Compute attention_score (0–10) for a single change.

    Factors:
      1. Rank band weight (higher ranks matter more)
      2. Change type (new_entry / dropped_out > rank move)
      3. Magnitude of change (big jumps = high attention)
      4. Breakout bonus (jumping from low rank is a stronger signal)
      5. Track relevance bonus (赛道游戏 +1.5)
    """
    score = 0.0

    # Current rank for band weighting
    rank = today_rank if today_rank is not None else yesterday_rank
    if rank is None:
        rank = 99  # fallback

    # ── 1. Rank band weight ──
    if rank <= 3:
        score += 5.0
    elif rank <= 5:
        score += 3.5
    elif rank <= 10:
        score += 2.0
    elif rank <= 30:
        score += 1.0
    elif rank <= 50:
        score += 0.5
    else:
        score += 0.2

    # ── 2. Change type ──
    if change_type == "new_entry":
        score += 2.0
        if rank is not None and rank <= 10:
            score += 3.0  # straight into top 10 = major event
        elif rank is not None and rank <= 50:
            score += 1.5
    elif change_type == "dropped_out":
        yr = yesterday_rank or 99
        if yr <= 10:
            score += 4.0  # dropped from high position = big event
        elif yr <= 30:
            score += 2.0
        else:
            score += 0.5
    elif change_type in ("up", "down"):
        delta = abs(rank_change or 0)

        # Head-area small moves still matter
        if rank is not None and rank <= 5 and delta >= 1:
            score += 1.5
        elif rank is not None and rank <= 10 and delta >= 3:
            score += 1.0

        # Magnitude bonus (regardless of band)
        if delta >= 20:
            score += 3.5
        elif delta >= 10:
            score += 2.0
        elif delta >= 5:
            score += 1.0
        else:
            score += 0.3

    # ── 3. Breakout bonus ──
    if change_type == "up":
        yr = yesterday_rank or 99
        delta = abs(rank_change or 0)
        if yr > 30 and delta >= 10:
            score += 2.0
        if yr > 50 and delta >= 5:
            score += 1.0

    # ── 5. Track relevance bonus ──
    if game_name:
        try:
            from src.pipeline.track_filter import classify_game
            if classify_game(game_name) == "track":
                score += 1.5
        except Exception as e:
            print(f"  [WARN] track classification failed for {game_name}: {e}", file=sys.stderr)
            pass

    return min(score, 10.0)


def is_significant(change_type: str, attention_score: float, today_rank: int | None) -> bool:
    """
    Determine if a change is significant enough to flag.

    Rules (per DESIGN.md §5.3.5):
      - attention_score >= 5.0 → significant (Story Picker candidate pool)
      - new_entry / dropped_out always flagged if in top 50
      - Top 5 moves always flagged
    """
    if attention_score >= 5.0:
        return True
    if change_type in ("new_entry", "dropped_out") and today_rank is not None and today_rank <= 50:
        return True
    if today_rank is not None and today_rank <= 5 and change_type in ("up", "down"):
        return True
    return False


# ── Day Classification ───────────────────────────────────────

def classify_day(
    total: int,
    up: int,
    down: int,
    new_entry: int,
    dropped_out: int,
    big_moves: int = 0,
) -> str:
    """
    Classify a day as quiet / normal / volatile.

    Per DESIGN.md §5.3.3:
      - quiet: ≤10% moved, ≤2 in/out, no big moves
      - volatile: ≥30% moved, or ≥8 in/out, or ≥5 big moves
      - normal: everything else
    """
    moved = up + down + new_entry + dropped_out
    volatility = moved / total if total > 0 else 0
    new_dropped = new_entry + dropped_out

    if volatility <= 0.1 and new_dropped <= 2 and big_moves == 0:
        return "quiet"
    elif volatility >= 0.3 or new_dropped >= 8 or big_moves >= 5:
        return "volatile"
    else:
        return "normal"


# ── Main Diff Logic ──────────────────────────────────────────

def _diff_one_chart(
    date: str,
    prev_date: str,
    today_rows: list[RankingEntry],
    yesterday_rows: list[RankingEntry],
) -> dict[str, Any]:
    """
    Diff a single (platform, chart_type) group.

    Returns overview dict + list of change dicts for this chart.
    """
    today_map: dict[str, dict[str, Any]] = {r["bundle_id"]: r for r in today_rows}
    yesterday_map: dict[str, dict[str, Any]] = {r["bundle_id"]: r for r in yesterday_rows}

    overview = {
        "total": len(today_rows),
        "up": 0,
        "down": 0,
        "new_entry": 0,
        "dropped_out": 0,
        "stable": 0,
        "big_moves": 0,
    }
    changes: list[ChangeRecord] = []

    # 1. Iterate today's rankings
    for bundle_id, t in today_map.items():
        y = yesterday_map.get(bundle_id)

        if y is None:
            overview["new_entry"] += 1
            change_type = "new_entry"
            today_rank = t["rank"]
            yesterday_rank = None
            rank_change = None
        elif t["rank"] < y["rank"]:
            overview["up"] += 1
            change_type = "up"
            today_rank = t["rank"]
            yesterday_rank = y["rank"]
            rank_change = y["rank"] - t["rank"]  # positive = up
            if abs(rank_change) >= 15:
                overview["big_moves"] += 1
        elif t["rank"] > y["rank"]:
            overview["down"] += 1
            change_type = "down"
            today_rank = t["rank"]
            yesterday_rank = y["rank"]
            rank_change = y["rank"] - t["rank"]  # negative = down
            if abs(rank_change) >= 15:
                overview["big_moves"] += 1
        else:
            overview["stable"] += 1
            continue  # no change record needed for stable

        attention_score = compute_attention_score(
            change_type, today_rank, yesterday_rank, rank_change,
            game_name=t.get("game_name", ""),
        )

        changes.append({
            "date": date,
            "platform": t["platform"],
            "chart_type": t["chart_type"],
            "bundle_id": bundle_id,
            "game_name": t["game_name"],
            "developer": t.get("developer"),
            "today_rank": today_rank,
            "yesterday_rank": yesterday_rank,
            "rank_change": rank_change,
            "change_type": change_type,
            "attention_score": round(attention_score, 1),
            "is_significant": is_significant(change_type, attention_score, today_rank),
        })

    # 2. Find dropped-out games (in yesterday but not today)
    for bundle_id, y in yesterday_map.items():
        if bundle_id not in today_map:
            overview["dropped_out"] += 1
            yesterday_rank = y["rank"]
            if yesterday_rank <= 30:
                overview["big_moves"] += 1

            attention_score = compute_attention_score(
                "dropped_out", None, yesterday_rank, None
            )

            changes.append({
                "date": date,
                "platform": y["platform"],
                "chart_type": y["chart_type"],
                "bundle_id": bundle_id,
                "game_name": y["game_name"],
                "developer": y.get("developer"),
                "today_rank": None,
                "yesterday_rank": yesterday_rank,
                "rank_change": None,
                "change_type": "dropped_out",
                "attention_score": round(attention_score, 1),
                "is_significant": is_significant("dropped_out", attention_score, None),
            })

    return {"overview": overview, "changes": changes}


def diff_with_yesterday(date: str) -> dict[str, Any]:
    """
    Compare today's rankings with yesterday's, per chart_type.

    Groups rankings by (platform, chart_type), diffs each group independently,
    then aggregates results.

    Returns a dict with:
      - date, prev_date, day_type, overview (aggregated), per_chart (detail), changes (all)

    If no yesterday data exists (Day 1), returns an empty result
    with a hint to import more data.
    """
    db = get_db()

    today_rows = db.get_rankings_by_date(date)
    if not today_rows:
        return {"date": date, "error": f"No ranking data found for {date}"}

    prev_date = db.get_previous_date(date)
    if prev_date is None:
        chart_types = sorted(set(r["chart_type"] for r in today_rows))
        return {
            "date": date,
            "day_type": "first_day",
            "overview": {"total": len(today_rows), "hint": "首日数据已入库，明日导入后可进行对比分析",
                         "chart_types": chart_types},
            "per_chart": {},
            "changes": [],
        }

    yesterday_rows = db.get_rankings_by_date(prev_date)

    # Group by (platform, chart_type)
    from collections import defaultdict
    today_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    yesterday_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for r in today_rows:
        today_groups[(r["platform"], r["chart_type"])].append(r)
    for r in yesterday_rows:
        yesterday_groups[(r["platform"], r["chart_type"])].append(r)

    # Diff each chart group independently
    all_changes: list[dict[str, Any]] = []
    per_chart: dict[str, dict[str, Any]] = {}
    agg_overview = {"total": 0, "up": 0, "down": 0, "new_entry": 0,
                    "dropped_out": 0, "stable": 0, "big_moves": 0}

    for (platform, ct), t_rows in sorted(today_groups.items()):
        y_rows = yesterday_groups.get((platform, ct), [])
        result = _diff_one_chart(date, prev_date, t_rows, y_rows)

        key = f"{platform}/{ct}"
        per_chart[key] = result["overview"]
        all_changes.extend(result["changes"])

        for k in agg_overview:
            agg_overview[k] += result["overview"][k]

    # 3. Sort by attention_score descending
    all_changes.sort(key=lambda c: c["attention_score"], reverse=True)

    # 4. Classify the day (aggregate all charts)
    day_type = classify_day(
        total=agg_overview["total"],
        up=agg_overview["up"],
        down=agg_overview["down"],
        new_entry=agg_overview["new_entry"],
        dropped_out=agg_overview["dropped_out"],
        big_moves=agg_overview["big_moves"],
    )

    # 5. Persist to database
    if all_changes:
        db.insert_changes(all_changes)
        # Re-read to get auto-increment IDs assigned by SQLite
        all_changes = db.get_changes_by_date(date)

    return {
        "date": date,
        "prev_date": prev_date,
        "day_type": day_type,
        "overview": agg_overview,
        "per_chart": per_chart,
        "changes": all_changes,
    }


# ── CLI test entry ───────────────────────────────────────────

if __name__ == "__main__":
    import json

    date_arg = sys.argv[2] if len(sys.argv) >= 4 and sys.argv[1] == "--date" else None
    if date_arg is None:
        # Default to latest date in DB
        db = get_db()
        dates = db.get_available_dates()
        if not dates:
            print("No data in database. Import a CSV first.")
            sys.exit(1)
        date_arg = dates[0]

    result = diff_with_yesterday(date_arg)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
