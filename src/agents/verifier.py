"""
Verifier (Agent B) — credibility check for Researcher findings.

Evaluates each finding on 3 dimensions (1-5 scale):
  1. Source authority  — official > media > self-media > forum > anonymous
  2. Cross-validation  — multiple independent sources corroborating
  3. Causal logic      — does the event plausibly explain the rank change?

Findings scoring ≥ 3.0 pass through to Analyst; < 3.0 are rejected but documented.

Usage:
    python -m src.agents.verifier --research-id 2
    python -m src.agents.verifier --findings '{"game":"...","findings":[...]}'
"""

from __future__ import annotations

import json
import sys
from typing import Any

from src.agents.base import Agent, Tool
from src.tools.web_search import web_search, TOOL_DESCRIPTOR as SEARCH_DESC
from src.tools.web_fetch import web_fetch, TOOL_DESCRIPTOR as FETCH_DESC


def build_agent(model: str | None = None) -> Agent:
    """Create a Verifier agent with search + fetch tools.

    Uses max_tool_rounds=6 — fewer than Researcher since we only do
    cross-validation searches (1-2 queries per finding, not 5 dimensions).
    """
    tools = [
        Tool(
            name=SEARCH_DESC["name"],
            description=SEARCH_DESC["description"],
            parameters=SEARCH_DESC["parameters"],
            fn=web_search,
        ),
        Tool(
            name=FETCH_DESC["name"],
            description=FETCH_DESC["description"],
            parameters=FETCH_DESC["parameters"],
            fn=web_fetch,
        ),
    ]
    return Agent(
        "verifier",
        tools=tools,
        model=model,
        max_tool_rounds=6,
        max_tokens=4096,
    )


def verify(
    research_result: dict[str, Any],
    rank_context: dict[str, Any] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run Verifier on a Researcher output.

    Args:
        research_result: Dict with 'game', 'bundle_id', 'findings' (list of finding dicts),
                         and optionally 'rank_context'.
        rank_context: Optional override for rank context (used to evaluate causal logic).
                      If None, uses research_result['rank_context'].
        verbose: Print tool call traces to stderr.

    Returns:
        dict with game, findings_verified, summary, _run_id.
    """
    game_name = research_result.get("game", "未知")
    bundle_id = research_result.get("bundle_id", "")
    findings = research_result.get("findings", [])

    if not findings:
        return {
            "game": game_name,
            "findings_verified": [],
            "summary": {
                "total_findings": 0,
                "passed": 0,
                "rejected": 0,
                "average_score": 0.0,
                "overall_assessment": "没有可核验的 findings",
            },
            "_run_id": "",
        }

    # Build rank context for causal logic evaluation
    ctx = rank_context or research_result.get("rank_context", {})
    yesterday_rank = ctx.get("yesterday_rank", "?")
    today_rank = ctx.get("today_rank", "?")
    change_type = ctx.get("change_type", "?")
    date = ctx.get("date", "")

    # Strip verbose fields from findings to reduce context size
    findings_for_llm = []
    for i, f in enumerate(findings, 1):
        sources_compact = []
        for s in f.get("sources", []):
            sources_compact.append({
                "url": s.get("url", ""),
                "title": s.get("title", ""),
                "source_type": s.get("source_type", "?"),
                "platform": s.get("platform", "?"),
                "fetch_status": s.get("fetch_status", "?"),
            })
        findings_for_llm.append({
            "index": i,
            "dimension": f.get("dimension", "?"),
            "headline": f.get("headline", "?"),
            "summary": (f.get("summary", "") or "")[:200],
            "sources": sources_compact,
            "confidence": f.get("confidence", "?"),
            "design_tags": f.get("design_tags", []),
        })

    findings_json = json.dumps(findings_for_llm, ensure_ascii=False, indent=2)

    agent = build_agent()
    result = agent.run(
        game_name=game_name,
        yesterday_rank=yesterday_rank,
        today_rank=today_rank,
        change_type=change_type,
        date=date,
        finding_count=len(findings),
        findings_json=findings_json,
        _verbose=verbose,
    )

    run_id = result.pop("_run_id", "")

    # ── Persist to DB (update the research result with verification) ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()

        # Find the research_result row for this game
        rows = db._connect().execute(
            "SELECT id FROM research_results ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            try:
                stored = db._connect().execute(
                    "SELECT findings_json FROM research_results WHERE id = ?",
                    (row["id"],)
                ).fetchone()
                stored_data = json.loads(stored["findings_json"])
                if (stored_data.get("bundle_id") == bundle_id
                        and stored_data.get("game") == game_name):
                    db.update_research_verification(
                        research_id=row["id"],
                        verified_json=json.dumps(result, ensure_ascii=False),
                    )
                    break
            except Exception:
                continue
    except Exception:
        pass  # persistence is best-effort

    result["_run_id"] = run_id
    return result


def verify_from_db(research_id: int, verbose: bool = False) -> dict[str, Any]:
    """Run Verifier on a research result stored in the database.

    Args:
        research_id: ID from the research_results table.
        verbose: Print tool call traces to stderr.

    Returns:
        Verification result dict.
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
    return verify(research_result, verbose=verbose)


# ── CLI test entry ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Verifier Agent — credibility check for Researcher findings"
    )
    parser.add_argument("--research-id", type=int, default=None,
                        help="Research result ID from the database")
    parser.add_argument("--findings", type=str, default=None,
                        help="JSON file path or JSON string of research result")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print tool call traces to stderr")
    args = parser.parse_args()

    if args.research_id is not None:
        result = verify_from_db(args.research_id, verbose=args.verbose)
    elif args.findings is not None:
        # Try as file path first, then as JSON string
        try:
            with open(args.findings, encoding="utf-8") as f:
                research_result = json.load(f)
        except (FileNotFoundError, OSError):
            research_result = json.loads(args.findings)
        result = verify(research_result, verbose=args.verbose)
    else:
        # Default: verify the latest research result
        from src.storage.sqlite import get_db
        db = get_db()
        rows = db._connect().execute(
            "SELECT id FROM research_results ORDER BY id DESC LIMIT 1"
        ).fetchall()
        if not rows:
            print("No research results in DB. Run researcher first.")
            sys.exit(1)
        result = verify_from_db(rows[0]["id"], verbose=args.verbose)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
