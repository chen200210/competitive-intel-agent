"""
Overview Scanner (Agent A0) — daily industry-wide scan.

Runs once per day regardless of change volume.
Searches for gaming industry news, provides context for downstream agents,
and decides which changes are worth deep research vs. noise.

Usage:
    python -m src.agents.overview_scanner --date 2026-06-16
"""

from __future__ import annotations

import json
import sys
from typing import Any

from src.agents.base import Agent, Tool
from src.tools.web_search import web_search, TOOL_DESCRIPTOR as SEARCH_DESC
from src.tools.web_fetch import web_fetch, TOOL_DESCRIPTOR as FETCH_DESC


def build_agent(model: str | None = None) -> Agent:
    """Create an Overview Scanner agent with search + fetch tools."""
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
    return Agent("overview_scanner", tools=tools, model=model)


def _build_overview_from_changes(
    changes: list[dict[str, Any]],
    rankings_total: int,
) -> dict[str, Any]:
    """Compute real overview stats from change records (not hardcoded zeros)."""
    overview = {
        "total": rankings_total,
        "up": 0,
        "down": 0,
        "new_entry": 0,
        "dropped_out": 0,
        "big_moves": 0,
    }
    for c in changes:
        ct = c.get("change_type", "")
        if ct in overview:
            overview[ct] += 1
        # Count big moves (rank_change >= 15)
        rc = c.get("rank_change")
        if rc is not None and abs(rc) >= 15:
            overview["big_moves"] += 1

    # Calculate volatility
    moved = overview["up"] + overview["down"] + overview["new_entry"] + overview["dropped_out"]
    overview["volatility"] = round(moved / overview["total"], 3) if overview["total"] > 0 else 0.0

    # Classify day
    nd = overview["new_entry"] + overview["dropped_out"]
    if overview["volatility"] <= 0.1 and nd <= 2 and overview["big_moves"] == 0:
        overview["day_type"] = "quiet"
    elif overview["volatility"] >= 0.3 or nd >= 8 or overview["big_moves"] >= 5:
        overview["day_type"] = "volatile"
    else:
        overview["day_type"] = "normal"

    return overview


def _build_story_pool_hint(
    date: str,
    story_pool: list[dict[str, Any]] | None = None,
) -> str:
    """Build a context hint about pre-identified story candidates.

    Tells the agent which stories the rule engine already flagged, so it can
    make an informed decision about the final 5–8 cut.

    Returns an empty string if no story pool is available.
    """
    # If story_pool not provided, try to compute from Story Picker
    if story_pool is None:
        try:
            from src.pipeline.story_picker import pick_stories_for_date
            pool_result = pick_stories_for_date(date)
            story_pool = pool_result.get("stories", [])
        except Exception:
            return ""

    if not story_pool:
        return ""

    # Separate cross-chart stories from single-chart stories
    single_chart = [s for s in story_pool if s.get("story_type") != "cross_chart_signal"]
    cross_chart = [s for s in story_pool if s.get("story_type") == "cross_chart_signal"]

    lines = [
        "",
        "--- 规则引擎预选故事池（供参考）---",
        f"Story Picker 预选了 {len(story_pool)} 条候选故事：",
        f"  单榜信号 {len(single_chart)} 条 + 跨榜信号 {len(cross_chart)} 条",
        "",
    ]

    if cross_chart:
        lines.append("⚠️ 跨榜信号（信息量最高，必须优先考虑）：")
        for i, s in enumerate(cross_chart, 1):
            headline = s.get("story_headline", s.get("game_name", "?"))
            threat = s.get("threat_level", "?")
            pattern = s.get("signal_pattern", s.get("story_type", "?"))
            lines.append(f"  ⚠️ #{i} [{pattern}] {headline}  (threat={threat})")
        lines.append("")

    if single_chart:
        lines.append("单榜信号（按关注度排序）：")
        for i, s in enumerate(single_chart, 1):
            stype = s.get("story_type", "unknown")
            headline = s.get("story_headline", s.get("game_name", "?"))
            attention = s.get("attention_score", "")
            lines.append(f"  #{i} [{stype}] {headline}  (attention={attention})")
        lines.append("")

    lines.append("以上是规则引擎的预判。你需要结合搜索到的行业新闻，从中选出 5-8 条真正值得深度调研的故事。")
    lines.append("⚠️ 跨榜信号（cross_chart_signal）的信息量远高于单榜信号——单榜告诉你'发生了什么'，跨榜告诉你'这意味着什么'。")
    lines.append("   至少选择 2 条跨榜信号进入 recommended_focus（如果当天有跨榜信号的话）。")
    lines.append("---")

    return "\n".join(lines)


def _build_cross_chart_context(
    cross_signals: list[dict[str, Any]] | None = None,
) -> str:
    """Build a prominent cross-chart context block — separate from the story pool.

    This is the "hard sell" for cross-chart signals. Unlike the story pool hint
    (which mixes all story types), this block is dedicated to making cross-chart
    signals impossible to miss.
    """
    if not cross_signals:
        return ""

    # Sort by threat level
    threat_order = {"high": 0, "medium": 1, "low": 2}
    sorted_signals = sorted(cross_signals, key=lambda s: threat_order.get(s.get("threat_level", "low"), 3))

    high_threat = [s for s in sorted_signals if s.get("threat_level") == "high"]
    medium_threat = [s for s in sorted_signals if s.get("threat_level") == "medium"]

    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════╗",
        "║  📐 跨榜对照信号 — 今日最重要的情报（必读）                   ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
        "同一个游戏在不同榜单上的位置差异，暴露了单榜看不出的深层信息：",
        "",
    ]

    if high_threat:
        lines.append("🔴 高威胁信号（必须进入 recommended_focus）：")
        lines.append("")
        for s in high_threat:
            lines.append(f"  游戏: {s.get('game_name', '?')}")
            lines.append(f"  榜单: {json.dumps(s.get('charts_json', {}), ensure_ascii=False)}")
            lines.append(f"  信号: {s.get('signal_pattern', '?')} — {s.get('signal_description', '?')}")
            lines.append(f"  为什么重要: {_signal_why(s)}")
            lines.append("")

    if medium_threat:
        lines.append("🟡 中威胁信号（建议至少选 1 条进入 recommended_focus）：")
        lines.append("")
        for s in medium_threat[:5]:  # top 5
            lines.append(f"  游戏: {s.get('game_name', '?')}")
            lines.append(f"  榜单: {json.dumps(s.get('charts_json', {}), ensure_ascii=False)}")
            lines.append(f"  信号: {s.get('signal_pattern', '?')} — {s.get('signal_description', '?')}")
            lines.append("")

    lines.append("── 跨榜信号速查表 ──")
    lines.append("| 信号类型 | 含义 | 为什么重要 |")
    lines.append("|---------|------|-----------|")
    lines.append("| leading | 多榜同步领先 | 产品全面爆发，做对了什么？必深度调研 |")
    lines.append("| traffic_leak | 获客强、变现弱 | 用户想要但不愿付费——你的付费设计机会 |")
    lines.append("| harvest | 小众高付费 | 抢你的大R，学它的商业化设计 |")
    lines.append("| word_of_mouth | 社区强于下载 | 领先指标——今天热、明天涨，最需要 Design Analyst 介入 |")
    lines.append("| divergence | 各榜背离 | 买量催的假繁荣，或老游戏续命。永远值得标记 |")
    lines.append("")
    lines.append("⚠️ 以上跨榜信号中，至少选择 2 条进入 recommended_focus。")
    lines.append("   这些不是「备选故事」——它们是你今天最重要的调研对象。")

    return "\n".join(lines)


def _signal_why(signal: dict[str, Any]) -> str:
    """Explain WHY a cross-chart signal matters for decision-making."""
    pattern = signal.get("signal_pattern", "")
    charts = signal.get("charts_json", {})
    game = signal.get("game_name", "")

    why = {
        "leading": f"{game}在多个榜单同步领先——产品全面爆发。调研它做对了什么（版本？活动？投放？），你的产品能不能学？",
        "traffic_leak": f"{game}获客强但变不了现——用户想要但不愿付费。这说明该品类存在商业化设计缺陷，恰是你的差异化机会。",
        "harvest": f"{game}用户不多但付费极强——抢的是你的大R。务必调研它的付费设计和留存机制。",
        "word_of_mouth": f"{game}社区热度远超下载量——今天在发酵，明天可能转下载。这是领先指标，需要 Design Analyst 拆解玩法亮点。",
        "divergence": f"{game}各榜数据背离——可能是买量催的虚假繁荣，也可能是运营事故。标记它，观察后续走势。",
    }
    return why.get(pattern, f"跨榜信号 {pattern}，需关注")


def _build_sector_context() -> str:
    """Load business_focus from competitor_list.yaml and format as a hard constraint.

    Tells the Scanner: "these genres and themes are your company's business focus.
    Games matching them must NOT be casually skipped."
    """
    from src.config import settings
    yaml_path = settings.competitor_list_path

    if not yaml_path.exists():
        return ""

    try:
        with open(yaml_path, encoding="utf-8") as f:
            import yaml as _yaml
            config = _yaml.safe_load(f)
        focus = config.get("business_focus", {})
    except Exception:
        return ""

    genres = focus.get("genres", [])
    themes = focus.get("themes", [])

    if not genres and not themes:
        return ""

    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════╗",
        "║  🎯 赛道聚焦 — 你们公司的业务方向（最高优先级）              ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
    ]

    if genres:
        lines.append(f"  目标玩法: {', '.join(genres)}")
    if themes:
        lines.append(f"  目标题材: {', '.join(themes)}")

    lines.extend([
        "",
        "  硬性规则（不可违反）：",
        "  1. 如果一款游戏的名称/品类/题材匹配以上任何关键词 → 它就不能被放入 skip_deep_research_for",
        "     即使排名波动看起来是'正常波动'或'榜单重置'，也必须放入 recommended_focus。",
        "  2. daily_overview 的 recommended_focus 中，至少要有 2 条与赛道相关",
        "     （塔防品类 或 微恐/冰河/火山题材）。如果当天确实没有匹配的变动，",
        "     在 skip 中注明「今日无赛道相关变动」。",
        "  3. 赛道匹配游戏在 recommended_focus 中的排序仅次于跨榜高威胁信号，",
        "     高于所有其他单榜信号。",
        "  4. 塔防品类关键词: 塔防、TD、Tower Defense、Kingdom Rush、王国保卫战、",
        "     保卫萝卜、地牢、 Dungeon 、Defense。只要游戏名或分类含这些词 → 匹配。",
        "",
        "  为什么: 你们公司的业务方向是塔防品类 + 微恐/冰河/火山题材。这些游戏的",
        "  每一次排名变动都是直接竞争情报，漏掉任何一条都意味着情报失误。",
        "",
    ])

    return "\n".join(lines)


def scan(
    date: str,
    platform: str = "iOS",
    overview: dict[str, Any] | None = None,
    changes: list[dict[str, Any]] | None = None,
    story_pool: list[dict[str, Any]] | None = None,
    cross_chart_signals: list[dict[str, Any]] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run the Overview Scanner for a given date.

    Args:
        date: Date string YYYY-MM-DD.
        platform: Platform filter (iOS / Android).
        overview: Day overview dict (auto-computed from changes if not provided).
        changes: List of changes from Differ (top 30 sent to LLM).
        story_pool: Pre-picked story candidates from Story Picker (including cross_chart
                    signals). Used as context for the agent's final 5–8 cut.
        cross_chart_signals: Raw cross-chart signals from Cross-Chart module.
                             Rendered as a prominent, hard-to-miss context block
                             separate from the story pool hint.
        verbose: Print tool call traces to stderr.

    Returns:
        dict with industry_news_today, volatility_context,
        recommended_focus (5–8 items), skip_deep_research_for.
    """
    from src.storage.sqlite import get_db
    db = get_db()

    # ── Gather data from DB if not provided ──
    if overview is None or changes is None:
        if changes is None:
            # Read ALL changes for this date (not just significant ones)
            changes = db.get_changes_by_date(date)
        if overview is None:
            rankings_total = len(db.get_rankings_by_date(date, platform=platform))
            overview = _build_overview_from_changes(changes, rankings_total)

    # ── Prepare input for LLM ──
    # Limit context: send top 30 changes by attention_score
    top_changes = sorted(changes, key=lambda c: c.get("attention_score", 0), reverse=True)[:30]

    # Build change list with explicit id + bundle_id fields
    changes_for_llm = []
    for c in top_changes:
        changes_for_llm.append({
            "change_id": c.get("id"),
            "game_name": c.get("game_name", ""),
            "bundle_id": c.get("bundle_id", ""),
            "developer": c.get("developer"),
            "chart_type": c.get("chart_type", ""),
            "today_rank": c.get("today_rank"),
            "yesterday_rank": c.get("yesterday_rank"),
            "rank_change": c.get("rank_change"),
            "change_type": c.get("change_type", ""),
            "attention_score": c.get("attention_score", 0),
            "is_significant": bool(c.get("is_significant", False)),
        })

    overview_json = json.dumps(overview, ensure_ascii=False)
    changes_json = json.dumps(changes_for_llm, ensure_ascii=False, indent=2)

    # ── Build story pool hint (5–8 range guidance) ──
    story_pool_hint = _build_story_pool_hint(date, story_pool)

    # ── Build cross-chart context (hard to miss) ──
    cross_chart_context = _build_cross_chart_context(cross_chart_signals)

    # ── Build sector context (business focus from competitor_list.yaml) ──
    sector_context = _build_sector_context()

    agent = build_agent()
    result = agent.run(
        date=date,
        platform=platform,
        overview_json=overview_json,
        changes_json=changes_json,
        story_pool_hint=story_pool_hint,
        cross_chart_context=cross_chart_context,
        sector_context=sector_context,
        _verbose=verbose,
    )

    # ── Enrich focus/skip items with bundle_id + change_id ──
    # Build lookup: change_id → {bundle_id, game_name, developer}
    change_lookup: dict[int, dict[str, Any]] = {}
    for c in top_changes:
        cid = c.get("id")
        if cid is not None:
            change_lookup[cid] = {
                "bundle_id": c.get("bundle_id", ""),
                "game_name": c.get("game_name", ""),
                "developer": c.get("developer"),
                "attention_score": c.get("attention_score", 0),
            }

    # Also build name-based fallback lookup (LLM might not always return change_id)
    name_lookup: dict[str, dict[str, Any]] = {}
    for c in top_changes:
        name = c.get("game_name", "")
        if name:
            name_lookup[name.lower()] = {
                "change_id": c.get("id"),
                "bundle_id": c.get("bundle_id", ""),
                "game_name": c.get("game_name", ""),
                "developer": c.get("developer"),
                "attention_score": c.get("attention_score", 0),
            }

    def _enrich(item: dict[str, Any]) -> dict[str, Any]:
        """Add bundle_id + change_id to a focus/skip item."""
        # Priority 1: explicit change_id from LLM
        cid = item.get("change_id")
        if cid is not None and cid in change_lookup:
            item.update(change_lookup[cid])
            return item

        # Priority 2: match by game name
        game = item.get("game", "")
        if game.lower() in name_lookup:
            item.update(name_lookup[game.lower()])
            return item

        # Priority 3: fuzzy match (game name contained in lookup key or vice versa)
        for key, val in name_lookup.items():
            if game.lower() in key or key in game.lower():
                item.update(val)
                return item

        # No match found — still add empty fields
        item.setdefault("bundle_id", "")
        item.setdefault("change_id", None)
        return item

    recommended = [_enrich(item) for item in result.get("recommended_focus", [])]
    skip_items = [_enrich(item) for item in result.get("skip_deep_research_for", [])]

    # ── Persist to database ──
    day_type = overview.get("day_type", "normal")
    volatility = overview.get("volatility", 0.0)
    run_id = result.pop("_run_id", "")

    db.upsert_daily_overview(
        date=date,
        day_type=day_type,
        volatility=volatility,
        industry_news_json=json.dumps(result.get("industry_news_today", []), ensure_ascii=False),
        recommended_focus_json=json.dumps(recommended, ensure_ascii=False),
        skip_json=json.dumps(skip_items, ensure_ascii=False),
        run_id=run_id,
    )

    # Return enriched result
    result["recommended_focus"] = recommended
    result["skip_deep_research_for"] = skip_items
    result["day_type"] = day_type
    result["volatility"] = volatility
    result["run_id"] = run_id
    return result


# ── CLI test entry ───────────────────────────────────────────

if __name__ == "__main__":
    date_arg = None
    verbose = False

    # Parse --date and --verbose from argv
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--date" and i + 1 < len(args):
            date_arg = args[i + 1]
            i += 2
        elif args[i] in ("--verbose", "-v"):
            verbose = True
            i += 1
        else:
            i += 1

    if date_arg is None:
        from src.storage.sqlite import get_db
        db = get_db()
        dates = db.get_available_dates()
        if not dates:
            print("No data in database. Import a CSV first.")
            print("Usage: python -m src.agents.overview_scanner --date 2026-06-16 [--verbose]")
            sys.exit(1)
        date_arg = dates[0]

    print(f"Running Overview Scanner for {date_arg}...")
    if verbose:
        print("[verbose mode ON — tool calls and intermediate results will be shown]\n", file=sys.stderr)
    result = scan(date_arg, verbose=verbose)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
