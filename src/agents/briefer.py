"""
Briefer (Agent E) — daily competitive intelligence report card.

Pure reasoning agent — synthesizes business analysis + design analysis
into a Feishu interactive card JSON.  Adjusts length based on day_type.

Usage:
    python -m src.agents.briefer --date 2026-06-16
"""

from __future__ import annotations

import json
import sys
from typing import Any

from src.agents.base import Agent


def build_agent(model: str | None = None) -> Agent:
    """Create a Briefer agent (no tools — pure formatting/composition)."""
    return Agent(
        "briefer",
        tools=None,
        model=model,
        max_tool_rounds=1,
        max_tokens=4096,
    )


def brief(
    date: str,
    day_type: str = "normal",
    overview: dict[str, Any] | None = None,
    business_analysis: dict[str, Any] | None = None,
    design_analysis: dict[str, Any] | None = None,
    sector_changes: list[dict[str, Any]] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate a Feishu card JSON for the daily report.

    Args:
        date: Date string YYYY-MM-DD.
        day_type: quiet / normal / volatile.
        overview: Day overview stats.
        business_analysis: Output from Analyst (business).
        design_analysis: Output from Design Analyst.
        sector_changes: Changes for games matching target sector (genre/themes).
        verbose: Print traces to stderr.

    Returns:
        Feishu card JSON dict with msg_type and card.
    """
    overview_json = json.dumps(overview or {}, ensure_ascii=False, indent=2)

    # Compact business analysis
    ba = business_analysis or {}
    ba_compact = {
        "overall_landscape": ba.get("overall_landscape", {}),
        "item_analyses": [
            {
                "game": item.get("game", ""),
                "rank_change": item.get("rank_change", ""),
                "trend_direction": item.get("trend_direction", ""),
                "analysis": item.get("analysis", {}),
            }
            for item in ba.get("item_analyses", [])
        ][:5],
        "target_sector_alert": ba.get("target_sector_alert", {}),
    }
    business_json = json.dumps(ba_compact, ensure_ascii=False, indent=2)

    # Compact design analysis
    da = design_analysis or {}
    da_compact = {}
    if da:
        da_obj = da.get("design_analysis", {})
        da_compact = {
            "core_highlight": {
                "title": da_obj.get("core_highlight", {}).get("title", ""),
                "what": da_obj.get("core_highlight", {}).get("what", ""),
                "why_it_works": da_obj.get("core_highlight", {}).get("why_it_works", ""),
            } if da_obj.get("core_highlight") else {},
            "takeaways": da_obj.get("takeaways", [])[:3],
            "market_viability": da_obj.get("market_viability", {}),
            "competitive_landscape": da_obj.get("competitive_landscape", {}),
            "risk_mirror": da_obj.get("risk_mirror", {}),
            "actionable_insight": da.get("actionable_insight", {}),
        }
    design_json = json.dumps(da_compact, ensure_ascii=False, indent=2)

    # Sector-relevant changes (games matching TD/微恐/冰河/火山)
    sector_json = json.dumps(sector_changes or [], ensure_ascii=False, indent=2)

    agent = build_agent()
    result = agent.run(
        date=date,
        day_type=day_type,
        overview_json=overview_json,
        business_analysis_json=business_json,
        design_analysis_json=design_json,
        sector_changes_json=sector_json,
        _verbose=verbose,
    )

    run_id = result.pop("_run_id", "")

    # ── Persist to DB ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        existing = db.get_analysis_report(date)
        if existing:
            db.upsert_analysis_report(
                date=date,
                research_ids=existing.get("research_ids", "[]"),
                report_json=existing.get("report_json", "{}"),
                design_analysis_json=existing.get("design_analysis_json", "{}"),
                brief_card_json=json.dumps(result, ensure_ascii=False),
            )
        else:
            db.upsert_analysis_report(
                date=date,
                research_ids="[]",
                report_json="{}",
                design_analysis_json="{}",
                brief_card_json=json.dumps(result, ensure_ascii=False),
            )
    except Exception:
        pass

    result["_run_id"] = run_id
    return result


def brief_from_db(date: str, verbose: bool = False) -> dict[str, Any]:
    """Run Briefer using data already in the database.

    Args:
        date: Date string YYYY-MM-DD.
        verbose: Print traces to stderr.

    Returns:
        Feishu card JSON dict.
    """
    from src.storage.sqlite import get_db
    db = get_db()

    # Get overview
    overview_data = db.get_daily_overview(date)
    day_type = "normal"
    overview = {}
    if overview_data:
        day_type = overview_data.get("day_type", "normal")
        overview = {
            "total": 0,
            "up": 0,
            "down": 0,
            "new_entry": 0,
            "dropped_out": 0,
            "day_type": day_type,
        }
        # Try to get actual numbers from the changes table
        changes = db.get_changes_by_date(date)
        if changes:
            overview["total"] = len(changes)
            overview["up"] = sum(1 for c in changes if c.get("change_type") == "up")
            overview["down"] = sum(1 for c in changes if c.get("change_type") == "down")
            overview["new_entry"] = sum(1 for c in changes if c.get("change_type") == "new_entry")
            overview["dropped_out"] = sum(1 for c in changes if c.get("change_type") == "dropped_out")

    # Get analysis report
    analysis = db.get_analysis_report(date)
    business_analysis = {}
    design_analysis = {}
    if analysis:
        try:
            business_analysis = json.loads(analysis.get("report_json", "{}"))
        except Exception:
            pass
        try:
            design_analysis = json.loads(analysis.get("design_analysis_json", "{}"))
        except Exception:
            pass

    # Find sector-relevant games (match target genres/themes from competitor_list.yaml)
    sector_changes = _find_sector_games(date, changes)

    return brief(
        date=date,
        day_type=day_type,
        overview=overview,
        business_analysis=business_analysis,
        design_analysis=design_analysis,
        sector_changes=sector_changes,
        verbose=verbose,
    )


def _find_sector_games(
    date: str,
    changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find changes for games matching target sector genres/themes.

    Loads competitor_list.yaml to get business_focus config, then scans
    changes for games whose name/developer/bundle_id suggest a match.
    Also enriches with any available research findings.
    """
    target_keywords: list[str] = [
        # Genre keywords
        "塔防", "TD", "tower defense", "Tower Defense",
        # Theme keywords
        "微恐", "恐怖", "horror", "冰河", "冰雪", "ice", "frozen",
        "火山", "volcano", "lava",
    ]

    # Try to load from YAML for more precise matching
    try:
        import yaml
        from src.config import settings
        yaml_path = settings.competitor_list_path
        if yaml_path.exists():
            with open(yaml_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            focus = config.get("business_focus", {})
            for g in focus.get("genres", []):
                if g not in target_keywords:
                    target_keywords.append(g)
            for t in focus.get("themes", []):
                if t not in target_keywords:
                    target_keywords.append(t)
    except Exception:
        pass

    sector: list[dict[str, Any]] = []
    from src.storage.sqlite import get_db
    db = get_db()

    for c in changes:
        game_name = c.get("game_name", "")
        developer = c.get("developer") or ""
        combined = f"{game_name} {developer}".lower()

        # Check if any keyword matches
        if any(kw.lower() in combined for kw in target_keywords):
            entry = {
                "game_name": game_name,
                "bundle_id": c.get("bundle_id", ""),
                "developer": developer,
                "change_type": c.get("change_type", ""),
                "today_rank": c.get("today_rank"),
                "yesterday_rank": c.get("yesterday_rank"),
                "rank_change": c.get("rank_change"),
                "attention_score": c.get("attention_score", 0),
            }
            # Try to attach research findings
            try:
                research_rows = db._connect().execute(
                    "SELECT findings_json FROM research_results WHERE change_id = ?",
                    (c.get("id"),)
                ).fetchall()
                if research_rows:
                    r = json.loads(research_rows[0]["findings_json"])
                    # Extract key findings with URLs
                    entry["research_highlights"] = [
                        {
                            "headline": f.get("headline", ""),
                            "summary": (f.get("summary") or "")[:200],
                            "urls": [s.get("url", "") for s in f.get("sources", []) if s.get("url")],
                        }
                        for f in r.get("findings", [])[:3]
                    ]
            except Exception:
                pass
            sector.append(entry)

    return sector


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Briefer Agent — daily Feishu card generator"
    )
    parser.add_argument("--date", type=str, default=None,
                        help="Date (YYYY-MM-DD)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print traces to stderr")
    args = parser.parse_args()

    date_arg = args.date
    if date_arg is None:
        from src.storage.sqlite import get_db
        db = get_db()
        dates = db.get_available_dates()
        if not dates:
            print("No data in database.")
            sys.exit(1)
        date_arg = dates[0]

    print(f"Generating daily brief for {date_arg}...")
    result = brief_from_db(date_arg, verbose=args.verbose)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
