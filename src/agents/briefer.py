"""
Briefer (Agent E) — daily competitive intelligence report card.

Pure reasoning agent — synthesizes all data sources (Scraper + Pipeline + Agents)
into a Feishu interactive card JSON with the new 6-section format.

Data flow:
  Scraper data → DB → briefer reads directly (no intermediate agents)
  Pipeline output → DB → briefer reads directly
  Agent output → DB → briefer reads directly

Usage:
    python -m src.agents.briefer --date 2026-06-22
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
        max_tokens=8192,
    )


def brief(
    date: str,
    day_type: str = "normal",
    overview: dict[str, Any] | None = None,
    design_analysis: dict[str, Any] | None = None,
    taptap_games: list[dict[str, Any]] | None = None,
    steam_ports: list[dict[str, Any]] | None = None,
    unreleased_games: list[dict[str, Any]] | None = None,
    market_news: list[dict[str, Any]] | None = None,
    sector_changes: list[dict[str, Any]] | None = None,
    cross_chart_signals: list[dict[str, Any]] | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Generate a Feishu card JSON for the daily report.

    New 5-section format:
      1. 📊 今日概况 — from OverviewScanner
      2. 🆕 新游关注 — Steam移植(必展) + TapTap赛道新游 + 未上线新游
      3. 📰 市场变动 — 游侠/17173 头条 + 赛道新闻
      4. 📊 排名变动 — 赛道游戏排名变化 + 跨榜信号
      5. 🎮 设计洞察 — from DesignAnalyst (no risk_mirror, no market_viability)

    Args:
        date: Date string YYYY-MM-DD.
        day_type: quiet / normal / volatile.
        overview: Day overview from OverviewScanner.
        design_analysis: Output from Design Analyst.
        taptap_games: TapTap new games from DB.
        steam_ports: Steam port games from DB.
        unreleased_games: Unreleased games from DB.
        market_news: News headlines from DB.
        sector_changes: Track-relevant rank changes.
        cross_chart_signals: Cross-chart signals from pipeline.
        verbose: Print traces to stderr.

    Returns:
        Feishu card JSON dict with msg_type and card.
    """

    # ── Compact scraper data ──
    taptap_json = json.dumps(_compact_taptap(taptap_games or []), ensure_ascii=False, indent=2)
    steam_json = json.dumps(_compact_steam(steam_ports or []), ensure_ascii=False, indent=2)
    unreleased_json = json.dumps(_compact_unreleased(unreleased_games or []), ensure_ascii=False, indent=2)
    news_json = json.dumps(_compact_news(market_news or []), ensure_ascii=False, indent=2)

    # ── Compact pipeline data ──
    overview_json = json.dumps(overview or {}, ensure_ascii=False, indent=2)
    sector_json = json.dumps(_compact_changes(sector_changes or []), ensure_ascii=False, indent=2)
    cross_json = json.dumps(_compact_cross(cross_chart_signals or []), ensure_ascii=False, indent=2)

    # ── Compact design analysis ──
    da = design_analysis or {}
    da_compact = {}
    if da:
        da_obj = da.get("design_analysis", {})
        da_compact = {
            "core_gameplay_breakdown": {
                "mechanism_chain": da_obj.get("core_gameplay_breakdown", {}).get("mechanism_chain", ""),
                "player_sentiment": da_obj.get("core_gameplay_breakdown", {}).get("player_sentiment", ""),
            } if da_obj.get("core_gameplay_breakdown") else {},
            "retention_transplant": da_obj.get("retention_transplant", {}),
        }
    design_json = json.dumps(da_compact, ensure_ascii=False, indent=2)

    agent = build_agent()
    result = agent.run(
        date=date,
        day_type=day_type,
        overview_json=overview_json,
        taptap_games_json=taptap_json,
        steam_ports_json=steam_json,
        unreleased_games_json=unreleased_json,
        market_news_json=news_json,
        sector_changes_json=sector_json,
        cross_chart_json=cross_json,
        design_analysis_json=design_json,
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
    """Run Briefer using all data already in the database.

    Reads from: daily_overviews, analysis_reports, taptap_new_games,
    steam_port_games, market_news, unreleased_games, changes, cross_chart_signals.

    Args:
        date: Date string YYYY-MM-DD.
        verbose: Print traces to stderr.

    Returns:
        Feishu card JSON dict.
    """
    from src.storage.sqlite import get_db
    db = get_db()

    # ── Overview from OverviewScanner ──
    overview_data = db.get_daily_overview(date)
    day_type = "normal"
    overview = {}
    if overview_data:
        day_type = overview_data.get("day_type", "normal")
        overview = {
            "day_type": day_type,
            "volatility": overview_data.get("volatility", 0),
            "recommended_focus_count": len(
                json.loads(overview_data.get("recommended_focus_json", "[]"))
            ),
        }

    # ── Design analysis from DesignAnalyst ──
    analysis = db.get_analysis_report(date)
    design_analysis = {}
    if analysis:
        try:
            design_analysis = json.loads(analysis.get("design_analysis_json", "{}"))
        except Exception:
            pass

    # ── Scraper data (直读 DB，不经 AI) ──
    taptap_games = db.get_taptap_games_by_date(date)
    steam_ports = db.get_steam_ports_by_date(date)
    unreleased = db.get_unreleased_games_by_date(date)
    market_news = db.get_market_news_by_date(date)

    # ── Pipeline data ──
    changes = db.get_changes_by_date(date)
    sector_changes = _filter_track_changes(changes)

    # Cross-chart signals
    cross_signals = []
    try:
        cross_rows = db._connect().execute(
            "SELECT signals_json FROM cross_chart_signals WHERE date = ?",
            (date,)
        ).fetchall()
        if cross_rows:
            for row in cross_rows:
                try:
                    signals = json.loads(row["signals_json"])
                    if isinstance(signals, list):
                        cross_signals.extend(signals)
                except Exception:
                    pass
    except Exception:
        pass

    return brief(
        date=date,
        day_type=day_type,
        overview=overview,
        design_analysis=design_analysis,
        taptap_games=taptap_games,
        steam_ports=steam_ports,
        unreleased_games=unreleased,
        market_news=market_news,
        sector_changes=sector_changes,
        cross_chart_signals=cross_signals,
        verbose=verbose,
    )


# ═════════════════════════════════════════════════════════════
# Compact helpers — trim scraper data to what the LLM needs
# ═════════════════════════════════════════════════════════════

def _compact_taptap(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only track-relevant TapTap new games for display."""
    return [
        {
            "game_name": g.get("game_name", ""),
            "downloads": g.get("downloads", ""),
            "rating": g.get("rating"),
            "tags": g.get("tags", ""),
            "taptap_url": g.get("taptap_url", ""),
            "track_relevant": True,
            "has_bundle_id": bool(g.get("bundle_id")),
        }
        for g in games
        if g.get("track_relevant")  # ← only track-relevant
    ]


def _compact_steam(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep display-relevant fields for Steam port games.

    Enriches with TapTap data (downloads, rating, tags) when available.
    """
    result = []
    for g in games:
        entry = {
            "game_name": g.get("game_name", ""),
            "genre": g.get("genre", ""),
            "gameplay_tags": g.get("gameplay_tags", ""),
        }
        # Try to enrich with TapTap data
        try:
            from src.storage.sqlite import get_db
            db = get_db()
            rows = db._connect().execute(
                "SELECT downloads, rating, tags, taptap_url FROM taptap_new_games"
                " WHERE game_name = ? AND date = (SELECT MAX(date) FROM taptap_new_games"
                " WHERE game_name = ?)",
                (g.get("game_name", ""), g.get("game_name", ""))
            ).fetchall()
            if rows:
                r = dict(rows[0])
                entry["downloads"] = r.get("downloads", "") or ""
                entry["rating"] = r.get("rating")
                entry["tags"] = r.get("tags", "") or ""
                entry["taptap_url"] = r.get("taptap_url", "") or ""
        except Exception:
            pass
        result.append(entry)
    return result


def _compact_unreleased(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only display-relevant fields for unreleased games."""
    return [
        {
            "game_name": g.get("game_name", ""),
            "developer": g.get("developer", ""),
            "genre": g.get("genre", ""),
            "status": g.get("status", ""),
            "release_date": g.get("release_date", ""),
        }
        for g in games
    ]


def _compact_news(news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only game-related news from 游侠/17173, max 5, track first."""
    game_media = ["游侠", "17173", "gamersky", "ali213", "3dm", "3DM", "游戏陀螺", "youxituoluo"]

    # Non-game keywords — filter out tech ads, sports, entertainment gossip
    non_game_keywords = [
        "AirPods", "iPhone", "iPad", "MacBook", "Apple Watch",
        "电动滑板车", "电视", "耳机", "音箱", "手表",
        "Prime Day", "特惠精选", "优惠", "折扣", "促销",
        "世界杯", "足球", "NBA", "英超", "西甲", "欧冠",
        "演唱会", "张靓颖", "明星", "八卦", "走光", "抄袭",
        "芝麻街", "Netflix", "电影", "预告", "剧透",
        "礼包", "广告", "赛马大会", "抢号",
    ]

    filtered = []
    for n in news:
        source = (n.get("source", "") or "").lower()
        url = (n.get("url", "") or "").lower()

        # Must be from game media
        if not any(m in source or m in url for m in game_media):
            continue

        headline = n.get("headline", "")

        # Skip non-game content
        if any(kw.lower() in headline.lower() for kw in non_game_keywords):
            continue

        filtered.append(n)

    # Sort: track_relevant first
    filtered.sort(key=lambda n: (0 if n.get("track_relevant") else 1, n.get("headline", "")))
    return [
        {
            "headline": n.get("headline", ""),
            "source": n.get("source", ""),
            "url": n.get("url", ""),
            "track_relevant": bool(n.get("track_relevant", False)),
        }
        for n in filtered[:5]  # max 5
    ]


def _compact_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep display-relevant fields for rank changes, with TapTap URLs."""
    # Load cached TapTap URLs
    taptap_urls: dict[str, str] = {}
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        rows = db._connect().execute(
            "SELECT game_name, taptap_url FROM taptap_new_games WHERE taptap_url != ''"
        ).fetchall()
        for r in rows:
            taptap_urls[r["game_name"]] = r["taptap_url"]
        rows2 = db._connect().execute(
            "SELECT key, value FROM kv_cache WHERE key LIKE 'taptap_url:%'"
        ).fetchall()
        for r in rows2:
            taptap_urls[r["key"].replace("taptap_url:", "")] = r["value"]
    except Exception:
        pass

    return [
        {
            "game_name": c.get("game_name", ""),
            "change_type": c.get("change_type", ""),
            "today_rank": c.get("today_rank"),
            "yesterday_rank": c.get("yesterday_rank"),
            "rank_change": c.get("rank_change"),
            "attention_score": c.get("attention_score", 0),
            "chart_type": c.get("chart_type", ""),
            "taptap_url": taptap_urls.get(c.get("game_name", ""), ""),
        }
        for c in changes[:20]
    ]


def _compact_cross(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only display-relevant fields for cross-chart signals."""
    return [
        {
            "game_name": s.get("game_name", ""),
            "signal_pattern": s.get("signal_pattern", ""),
            "signal_description": s.get("signal_description", ""),
            "threat_level": s.get("threat_level", ""),
            "charts_json": s.get("charts_json", {}),
        }
        for s in signals
    ]


def _filter_track_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter changes to track-relevant games using keyword matching.

    Matches game names against track keywords (塔防, 肉鸽, etc.) and
    monitored_games from competitor_list.yaml.  For more precise filtering
    with genre/tags, use track_filter.classify_game() at the pipeline level.
    """
    # Base keywords
    track_keywords: list[str] = [
        "塔防", "TD", "tower defense", "Tower Defense",
        "肉鸽", "Roguelike", "Roguelite",
    ]

    # Load monitored games from YAML
    monitored_names: set[str] = set()
    try:
        import yaml
        from src.config import settings
        yaml_path = settings.competitor_list_path
        if yaml_path.exists():
            with open(yaml_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            for g in config.get("monitored_games", []):
                name = g.get("name", "") if isinstance(g, dict) else str(g)
                if name:
                    monitored_names.add(name.lower())
    except Exception:
        pass

    filtered = []
    for c in changes:
        game_name = c.get("game_name", "")
        if not game_name:
            continue

        # Check monitored list first
        if game_name.lower() in monitored_names:
            filtered.append(c)
            continue

        # Check keyword match in game name
        name_lower = game_name.lower()
        if any(kw.lower() in name_lower for kw in track_keywords):
            filtered.append(c)

    return filtered


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
