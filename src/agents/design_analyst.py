"""
Design Analyst (Agent C₂) — gameplay design & business decision analysis.

Uses web_search to find specific market data, competitor intel, and player
feedback. Analyzes game design from a decision-maker's perspective.
Answers: why is it fun, is this direction worth pursuing, what are the risks.

Usage:
    python -m src.agents.design_analyst --research-id 2
"""

from __future__ import annotations

import json
import sys
from typing import Any

from src.agents.base import Agent, Tool
from src.tools.web_search import web_search, TOOL_DESCRIPTOR as SEARCH_DESC


def build_agent(model: str | None = None) -> Agent:
    """Create a Design Analyst agent with web_search for market data."""
    tools = [
        Tool(
            name=SEARCH_DESC["name"],
            description=SEARCH_DESC["description"],
            parameters=SEARCH_DESC["parameters"],
            fn=web_search,
        ),
    ]
    return Agent(
        "design_analyst",
        tools=tools,
        model=model,
        max_tool_rounds=6,
        max_tokens=8192,  # deep analysis needs large output
    )


def analyze(
    research_result: dict[str, Any],
    target_sector: dict[str, Any] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run design analysis on a single game's research findings.

    Args:
        research_result: Researcher output dict with findings, in_development_signals, etc.
        target_sector: Target sector config (genres, themes, etc.) from competitor_list.yaml.
        verbose: Print traces to stderr.

    Returns:
        Design analysis dict.
    """
    game_name = research_result.get("game", "未知")
    developer = research_result.get("developer", "未知")
    bundle_id = research_result.get("bundle_id", "")
    date = research_result.get("rank_context", {}).get("date", "")
    ctx = research_result.get("rank_context", {})
    rank_context = (
        f"第{ctx.get('yesterday_rank','?')}→第{ctx.get('today_rank','?')}位, "
        f"{ctx.get('change_type','?')}"
    )

    # Extract design-tagged findings
    design_findings = []
    for f in research_result.get("findings", []):
        tags = f.get("design_tags", [])
        if tags:
            design_findings.append({
                "headline": f.get("headline", ""),
                "dimension": f.get("dimension", ""),
                "summary": (f.get("summary") or "")[:250],
                "design_tags": tags,
                "sources": [{
                    "url": s.get("url", ""),
                    "title": s.get("title", ""),
                    "source_type": s.get("source_type", ""),
                } for s in f.get("sources", [])],
                "confidence": f.get("confidence", ""),
            })

    in_dev = research_result.get("in_development_signals", [])

    # Target sector defaults (aligned with competitor_list.yaml)
    sector = target_sector or {
        "genres": ["塔防", "TD", "Tower Defense", "肉鸽", "Roguelike", "Roguelite"],
        "themes": [],
        "note": "关注塔防品类 + 肉鸽品类",
    }

    agent = build_agent()
    result = agent.run(
        game_name=game_name,
        developer=developer,
        bundle_id=bundle_id,
        rank_context=rank_context,
        date=date,
        design_findings_json=json.dumps(design_findings, ensure_ascii=False, indent=2),
        in_development_json=json.dumps(in_dev, ensure_ascii=False, indent=2),
        target_sector_json=json.dumps(sector, ensure_ascii=False, indent=2),
        _verbose=verbose,
    )

    run_id = result.pop("_run_id", "")

    # ── Persist to DB ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        # Find the analysis_report for this date and update design_analysis_json
        if date:
            existing = db.get_analysis_report(date)
            if existing:
                db.upsert_analysis_report(
                    date=date,
                    research_ids=existing.get("research_ids", "[]"),
                    report_json=existing.get("report_json", "{}"),
                    design_analysis_json=json.dumps(result, ensure_ascii=False),
                )
    except Exception:
        pass

    result["_run_id"] = run_id
    return result


def analyze_from_db(research_id: int, verbose: bool = False) -> dict[str, Any]:
    """Run Design Analyst on a stored research result.

    Args:
        research_id: ID from research_results table.
        verbose: Print traces to stderr.

    Returns:
        Design analysis dict.
    """
    from src.storage.sqlite import get_db
    db = get_db()

    row = db._connect().execute(
        "SELECT findings_json FROM research_results WHERE id = ?",
        (research_id,)
    ).fetchone()

    if not row:
        return {"error": f"Research result id={research_id} not found"}

    research_result = json.loads(row["findings_json"])

    # Load target sector from competitor_list.yaml if available
    target_sector = None
    try:
        import yaml
        from src.config import settings
        yaml_path = settings.competitor_list_path
        if yaml_path.exists():
            with open(yaml_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            target_sector = config.get("business_focus", None)
    except Exception:
        pass

    return analyze(research_result, target_sector=target_sector, verbose=verbose)


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    # Force UTF-8 on Windows
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Design Analyst — gameplay design & decision analysis"
    )
    parser.add_argument("--research-id", type=int, default=None,
                        help="Research result ID from the database")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print traces to stderr")
    args = parser.parse_args()

    if args.research_id is not None:
        result = analyze_from_db(args.research_id, verbose=args.verbose)
    else:
        from src.storage.sqlite import get_db
        db = get_db()
        rows = db._connect().execute(
            "SELECT id FROM research_results ORDER BY id DESC LIMIT 1"
        ).fetchall()
        if not rows:
            print("No research results in DB.")
            sys.exit(1)
        result = analyze_from_db(rows[0]["id"], verbose=args.verbose)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
