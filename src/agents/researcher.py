"""
Researcher (Agent A1) — deep-dive investigation of a single ranking change.

Triggered only for changes recommended by Overview Scanner.
Searches across 5 dimensions: event, gameplay, player, design, in_development.
Each dimension covers ≥2 platforms (TapTap, B站, 小红书, 微博, NGA/贴吧).

Usage:
    python -m src.agents.researcher --change '{"game_name":"鸣潮","bundle_id":"com.kurogame.mingchao",...}'
"""

from __future__ import annotations

import json
import sys
from typing import Any

from src.agents.base import Agent, Tool
from src.tools.web_search import web_search, TOOL_DESCRIPTOR as SEARCH_DESC
from src.tools.web_fetch import web_fetch, TOOL_DESCRIPTOR as FETCH_DESC
from src.tools.db_query import db_query, TOOL_DESCRIPTOR as DB_DESC
from src.tools.image_fetch import image_fetch, TOOL_DESCRIPTOR as IMG_DESC


# ── Agent factory ───────────────────────────────────────────────

def build_agent(model: str | None = None) -> Agent:
    """Create a Researcher agent with search + fetch + db_query tools.

    Uses max_tool_rounds=12 because the agent needs to cover 5 dimensions
    with multiple queries + web_fetch follow-ups per dimension.
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
        Tool(
            name=DB_DESC["name"],
            description=DB_DESC["description"],
            parameters=DB_DESC["parameters"],
            fn=db_query,
        ),
        Tool(
            name=IMG_DESC["name"],
            description=IMG_DESC["description"],
            parameters=IMG_DESC["parameters"],
            fn=image_fetch,
        ),
    ]
    return Agent(
        "researcher",
        tools=tools,
        model=model,
        max_tool_rounds=12,
        max_tokens=8192,  # Researcher outputs are large (8+ findings with sources)
    )


# ── Helpers ─────────────────────────────────────────────────────

def _safe_str(value: Any, default: str = "未知") -> str:
    """Convert value to string, handling None."""
    if value is None:
        return default
    return str(value)


def _safe_int(value: Any, default: int | str = "N/A") -> int | str:
    """Convert value to int, handling None."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _build_search_cache_hint(game_name: str) -> str:
    """Query search_cache + fetch_cache for recent activity related to this game.

    Returns a formatted hint string for the agent, listing recently tried queries
    and known-good / known-bad URLs. Empty string if no cache data is available.
    """
    try:
        from src.storage.sqlite import get_db
        db = get_db()

        # Recent searches (last 7 days, filtered by game name)
        with db._connect() as conn:
            searches = conn.execute(
                """SELECT query, engine, result_count, searched_at
                   FROM search_cache
                   WHERE datetime(searched_at) > datetime('now', '-7 days')
                   ORDER BY searched_at DESC LIMIT 15"""
            ).fetchall()

            # Recent fetches with status info
            fetches = conn.execute(
                """SELECT url, status_code, text_length, title, fetched_at
                   FROM fetch_cache
                   WHERE datetime(fetched_at) > datetime('now', '-7 days')
                   ORDER BY fetched_at DESC LIMIT 30"""
            ).fetchall()

        if not searches and not fetches:
            return ""

        lines = [
            "",
            "--- 搜索缓存参考（避免重复踩坑）---",
            "",
        ]

        if searches:
            lines.append("**近期已搜过的 query**（可参考，不必完全重复）：")
            for s in searches[:10]:
                lines.append(f"  - [{s['engine']}] \"{s['query']}\" → {s['result_count']} 条结果 ({s['searched_at']})")
            lines.append("")

        if fetches:
            # Split into good and bad
            good = [f for f in fetches if f["status_code"] == 200 and (f["text_length"] or 0) > 100]
            bad = [f for f in fetches if f["status_code"] != 200 or (f["text_length"] or 0) == 0]

            if bad:
                lines.append("**❌ 已知不可达的 URL**（不要再次 fetch）：")
                for f in bad[:10]:
                    reason = f"HTTP {f['status_code']}" if f["status_code"] != 200 else "返回空文本"
                    lines.append(f"  - {f['url'][:100]} → {reason}")
                lines.append("")

            if good:
                lines.append("**✅ 已验证可读的 URL**（可直接引用，节省一次 fetch）：")
                for f in good[:10]:
                    title = (f["title"] or "")[:60]
                    lines.append(f"  - {f['url'][:100]}")
                    if title:
                        lines.append(f"    标题: {title}")
                lines.append("")

        lines.append("在搜索时：")
        lines.append("  1. 参考已有搜索记录，换不同角度构造新 query，不要完全重复")
        lines.append("  2. 避免 fetch 已知不可达的 URL")
        lines.append("  3. 已验证可读的 URL 可以直接用作来源（但仍需至少 1 次新的 web_fetch 确认内容）")
        lines.append("---")

        return "\n".join(lines)

    except Exception:
        return ""


# ── Main entry point ────────────────────────────────────────────

def research(
    change: dict[str, Any],
    context_from_scanner: str = "",
    verbose: bool = False,
) -> dict[str, Any]:
    """Run the Researcher on a single change item.

    Args:
        change: Change dict with fields:
            - game_name (str)
            - bundle_id (str)
            - developer (str | None)
            - today_rank (int | None)
            - yesterday_rank (int | None)
            - rank_change (int | None)
            - change_type (str: up/down/new_entry/dropped_out)
            - date (str: YYYY-MM-DD)
            - platform (str, default "iOS")
        context_from_scanner: Overview Scanner's reason for recommending this change.
        verbose: Print tool call traces to stderr.

    Returns:
        dict with game, bundle_id, rank_context, historical_trend, findings,
        in_development_signals, search_coverage, _run_id.
    """
    game_name = _safe_str(change.get("game_name"), "未知游戏")
    bundle_id = _safe_str(change.get("bundle_id"), "")
    developer = _safe_str(change.get("developer"), "未知开发商")
    platform = _safe_str(change.get("platform"), "iOS")
    today_rank = _safe_int(change.get("today_rank"))
    yesterday_rank = _safe_int(change.get("yesterday_rank"))
    rank_change = _safe_int(change.get("rank_change"))
    change_type = _safe_str(change.get("change_type"), "up")
    date = _safe_str(change.get("date"), "")

    # Format rank display strings
    today_str = f"第{today_rank}位" if isinstance(today_rank, int) else "已掉榜"
    yesterday_str = f"第{yesterday_rank}位" if isinstance(yesterday_rank, int) else "新上榜"
    change_str = {
        "up": f"↑{rank_change}",
        "down": f"↓{abs(rank_change) if isinstance(rank_change, int) else '?'}",
        "new_entry": "新上榜",
        "dropped_out": "掉榜",
    }.get(change_type, str(rank_change))

    # Build context from scanner
    scanner_context = context_from_scanner or (
        f"{change_str}的排名变动，建议深度调研原因"
    )

    # Build search cache hint (recent searches + known good/bad URLs)
    search_cache_hint = _build_search_cache_hint(game_name)

    agent = build_agent()
    result = agent.run(
        game_name=game_name,
        bundle_id=bundle_id,
        developer=developer,
        platform=platform,
        today_rank=today_rank,
        yesterday_rank=yesterday_rank,
        rank_change=rank_change,
        change_type=change_type,
        date=date,
        context_from_scanner=scanner_context,
        search_cache_hint=search_cache_hint,
        _verbose=verbose,
    )

    run_id = result.pop("_run_id", "")

    # ── Enrich with input context ──
    result.setdefault("game", game_name)
    result.setdefault("bundle_id", bundle_id)
    result.setdefault("developer", developer)
    result.setdefault("rank_context", {
        "today_rank": today_rank if today_rank != "N/A" else None,
        "yesterday_rank": yesterday_rank if yesterday_rank != "N/A" else None,
        "rank_change": rank_change if rank_change != "N/A" else None,
        "change_type": change_type,
        "date": date,
    })

    # ── Persist to database ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()

        # Find the change_id if available
        change_id = change.get("id") or change.get("change_id")
        if change_id is None and date and bundle_id:
            # Try to look up the change_id from the changes table
            try:
                all_changes = db.get_changes_by_date(date)
                for c in all_changes:
                    if c.get("bundle_id") == bundle_id:
                        change_id = c.get("id")
                        break
            except Exception:
                pass

        if change_id is not None:
            db.insert_research_result(
                change_id=int(change_id),
                findings_json=json.dumps(result, ensure_ascii=False),
            )
    except Exception:
        pass  # persistence is best-effort

    result["_run_id"] = run_id
    return result


# ── Batch research ──────────────────────────────────────────────

def research_batch(
    focus_items: list[dict[str, Any]],
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Run Researcher on multiple focus items (sequential, not parallel).

    Args:
        focus_items: List of change dicts, each optionally with 'context_from_scanner'.
        verbose: Print progress to stderr.

    Returns:
        List of research result dicts (same order as input).
    """
    results: list[dict[str, Any]] = []
    for i, item in enumerate(focus_items):
        if verbose:
            game = item.get("game_name", item.get("game", f"item {i+1}"))
            print(f"[researcher] ({i+1}/{len(focus_items)}) {game}...", file=sys.stderr)
        context = item.pop("context_from_scanner", item.pop("reason", ""))
        result = research(item, context_from_scanner=context, verbose=verbose)
        results.append(result)
    return results


# ── CLI test entry ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Researcher Agent — deep-dive on a ranking change")
    parser.add_argument("--change", type=str, default=None,
                        help="JSON string of the change to research")
    parser.add_argument("--change-id", type=int, default=None,
                        help="Change ID from the database")
    parser.add_argument("--date", type=str, default=None,
                        help="Date (YYYY-MM-DD); used with --game to look up change")
    parser.add_argument("--game", type=str, default=None,
                        help="Game name or bundle_id; used with --date to look up change")
    parser.add_argument("--context", type=str, default="",
                        help="Context string from Overview Scanner")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print tool call traces to stderr")
    args = parser.parse_args()

    change: dict[str, Any] = {}

    if args.change:
        change = json.loads(args.change)
    elif args.change_id is not None:
        from src.storage.sqlite import get_db
        db = get_db()
        # Look up the change by ID
        all_dates = db.get_available_dates()
        found = None
        for d in all_dates:
            for c in db.get_changes_by_date(d):
                if c.get("id") == args.change_id:
                    found = c
                    break
            if found:
                break
        if found:
            change = found
        else:
            print(f"Change ID {args.change_id} not found in database.")
            sys.exit(1)
    elif args.date and args.game:
        from src.storage.sqlite import get_db
        db = get_db()
        for c in db.get_changes_by_date(args.date):
            if (args.game.lower() in c.get("game_name", "").lower()
                    or args.game == c.get("bundle_id")):
                change = c
                break
        if not change:
            print(f"No change found for '{args.game}' on {args.date}.")
            sys.exit(1)
    else:
        # Default: pick the highest-attention change from the latest date
        from src.storage.sqlite import get_db
        db = get_db()
        dates = db.get_available_dates()
        if not dates:
            print("No data in database. Import a CSV first.")
            print("Usage: python -m src.agents.researcher --change '{\"game_name\":\"...\",...}'")
            sys.exit(1)
        latest = dates[0]
        changes = db.get_changes_by_date(latest)
        if changes:
            change = changes[0]  # highest attention_score (already sorted)
        else:
            print(f"No changes found for {latest}. Try a different date with --date.")
            sys.exit(1)

    game_name = change.get("game_name", "?")
    print(f"Running Researcher for: {game_name}")
    if args.verbose:
        print("[verbose mode ON — tool calls will be shown]\n", file=sys.stderr)

    result = research(change, context_from_scanner=args.context, verbose=args.verbose)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
