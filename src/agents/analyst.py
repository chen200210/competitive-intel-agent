"""
Analyst (Agent C) — business analysis of ranking changes.

Pure reasoning agent — no external tools.  Synthesizes pre-gathered data
(research + verification + industry context + historical trends) into
structured analysis: causality, persistence, competition dynamics, impact.

Usage:
    python -m src.agents.analyst --date 2026-06-16
"""

from __future__ import annotations

import json
import sys
from typing import Any

from src.agents.base import Agent


def build_agent(model: str | None = None) -> Agent:
    """Create an Analyst agent with no tools (pure reasoning).

    No tools needed — all data is pre-gathered and provided in the input.
    Single-turn JSON output.
    """
    return Agent(
        "analyst",
        tools=None,
        model=model,
        max_tool_rounds=1,
        max_tokens=4096,
    )


def _compact_finding(f: dict[str, Any]) -> dict[str, Any]:
    """Strip a verified finding down to what the Analyst needs."""
    return {
        "headline": f.get("original_headline") or f.get("headline", ""),
        "dimension": f.get("dimension", ""),
        "summary": (f.get("summary") or "")[:200],
        "confidence": f.get("confidence", ""),
        "design_tags": f.get("design_tags", []),
        "verification": {
            "verdict": f.get("verdict", ""),
            "total_score": f.get("total_score"),
            "notes": (f.get("verification_notes") or "")[:150],
        } if f.get("verdict") else None,
    }


def _compact_research(r: dict[str, Any]) -> dict[str, Any]:
    """Strip a research result down to the essentials."""
    findings = r.get("findings", [])
    verified_list = r.get("findings_verified", [])
    in_dev = r.get("in_development_signals", [])

    return {
        "game": r.get("game", ""),
        "bundle_id": r.get("bundle_id", ""),
        "developer": r.get("developer", ""),
        "historical_trend": r.get("historical_trend", {}),
        "key_findings": [_compact_finding(f) for f in findings[:5]],
        "verification_summary": {
            "passed": sum(1 for v in verified_list if v.get("verdict") == "pass"),
            "rejected": sum(1 for v in verified_list if v.get("verdict") == "reject"),
            "average_score": (
                sum(v.get("total_score", 0) for v in verified_list) / len(verified_list)
                if verified_list else 0
            ),
        } if verified_list else None,
        "in_development_count": len(in_dev),
    }


def analyze(
    date: str,
    focus_items: list[dict[str, Any]],
    industry_context: dict[str, Any] | None = None,
    day_type: str = "normal",
    platform: str = "iOS",
    non_focus_changes: list[dict[str, Any]] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run business analysis on ranking changes.

    Args:
        date: Date string YYYY-MM-DD.
        focus_items: List of dicts, each with 'change' (the change record)
                     and 'research' (the verified research result).
        industry_context: Output from Overview Scanner.
        day_type: quiet / normal / volatile.
        platform: iOS / Android.
        non_focus_changes: Other changes for cross-game pattern detection.
        verbose: Print tool call traces to stderr.

    Returns:
        dict with overall_landscape, item_analyses, target_sector_alert, _run_id.
    """
    # Compact the data to avoid blowing context
    industry_json = json.dumps(industry_context or {}, ensure_ascii=False, indent=2)

    focus_compacted = []
    for item in focus_items:
        change = item.get("change", {})
        research = item.get("research", {})
        focus_compacted.append({
            "change": {
                "game": change.get("game_name") or change.get("game", ""),
                "bundle_id": change.get("bundle_id", ""),
                "developer": change.get("developer", ""),
                "today_rank": change.get("today_rank"),
                "yesterday_rank": change.get("yesterday_rank"),
                "rank_change": change.get("rank_change"),
                "change_type": change.get("change_type", ""),
                "attention_score": change.get("attention_score", 0),
            },
            "research": _compact_research(research) if research else None,
        })
    focus_json = json.dumps(focus_compacted, ensure_ascii=False, indent=2)

    non_focus_compacted = []
    for c in (non_focus_changes or []):
        non_focus_compacted.append({
            "game": c.get("game_name") or c.get("game", ""),
            "rank_change": c.get("rank_change"),
            "change_type": c.get("change_type", ""),
            "today_rank": c.get("today_rank"),
            "note": c.get("note", ""),
        })
    non_focus_json = json.dumps(non_focus_compacted, ensure_ascii=False, indent=2)

    agent = build_agent()
    result = agent.run(
        date=date,
        day_type=day_type,
        platform=platform,
        industry_context_json=industry_json,
        focus_items_json=focus_json,
        non_focus_changes_json=non_focus_json,
        _verbose=verbose,
    )

    run_id = result.pop("_run_id", "")

    # ── Persist to database ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        research_ids = json.dumps(
            [item.get("research", {}).get("_research_id", 0) for item in focus_items],
            ensure_ascii=False,
        )
        db.upsert_analysis_report(
            date=date,
            research_ids=research_ids,
            report_json=json.dumps(result, ensure_ascii=False),
        )
    except Exception:
        pass

    result["_run_id"] = run_id
    return result


def analyze_from_db(
    date: str,
    platform: str = "iOS",
    verbose: bool = False,
) -> dict[str, Any]:
    """Run Analyst using data already in the database.

    Gathers changes, research results, verification results, and
    industry context from the database, then runs analysis.

    Args:
        date: Date string YYYY-MM-DD.
        platform: Platform filter.
        verbose: Print tool call traces to stderr.

    Returns:
        Analysis result dict.
    """
    from src.storage.sqlite import get_db
    db = get_db()

    # Gather changes with attention_score >= 5.0
    all_changes = db.get_changes_by_date(date)
    significant = [c for c in all_changes if c.get("attention_score", 0) >= 5.0]
    non_significant = [c for c in all_changes if c.get("attention_score", 0) < 5.0]

    # Get industry context from daily_overviews
    overview = db.get_daily_overview(date)
    industry_context = {}
    if overview:
        try:
            industry_context = {
                "day_type": overview.get("day_type", "normal"),
                "volatility": overview.get("volatility", 0),
                "news": json.loads(overview.get("industry_news_json", "[]")),
                "recommended_focus": json.loads(overview.get("recommended_focus_json", "[]")),
            }
        except Exception:
            pass
    day_type = overview.get("day_type", "normal") if overview else "normal"

    # Gather research results for significant changes
    focus_items = []
    research_rows = db._connect().execute(
        "SELECT id, change_id, findings_json, verified_json FROM research_results"
    ).fetchall()

    research_by_change: dict[int, dict[str, Any]] = {}
    for row in research_rows:
        cid = row["change_id"]
        if cid is not None:
            try:
                r = json.loads(row["findings_json"])
                # Attach verification if available
                if row["verified_json"]:
                    try:
                        v = json.loads(row["verified_json"])
                        r["findings_verified"] = v.get("findings_verified", [])
                    except Exception:
                        pass
                r["_research_id"] = row["id"]
                research_by_change[cid] = r
            except Exception:
                continue

    for c in significant:
        cid = c.get("id")
        focus_items.append({
            "change": c,
            "research": research_by_change.get(cid, {}) if cid else {},
        })

    return analyze(
        date=date,
        focus_items=focus_items,
        industry_context=industry_context,
        day_type=day_type,
        platform=platform,
        non_focus_changes=non_significant,
        verbose=verbose,
    )


# ── CLI test entry ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyst Agent — business analysis of ranking changes"
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

    print(f"Running Analyst for {date_arg}...")
    result = analyze_from_db(date_arg, verbose=args.verbose)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
