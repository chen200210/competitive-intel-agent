"""
Briefer (Agent E) — daily competitive intelligence report card.

Card assembly is done entirely in CODE.  New games, ranking, and market
sections are all pre-built markdown — zero AI involvement, zero
hallucination risk.

Data flow:
  Scraper data → DB → briefer reads directly (no intermediate agents)
  Pipeline output → DB → briefer reads directly
  AI (summarizer) scores + summarizes news → briefer assembles markdown → card JSON

Orchestration only — rendering, news pipeline, AI scoring, enrichment, and
dedup are delegated to sibling modules:
  render.py          — markdown generation (zero AI)
  market_pipeline.py — rule-based news filtering + diversity selection
  scorer.py          — AI summarization + scoring (pos_label/neg_label from Matched Verdict Pool)
  enrichment.py      — article body fetch + image collection
  dedup.py           — reported_items read/write

Usage:
    python -m src.agents.briefer --date 2026-06-22
"""

from __future__ import annotations

import json
import sys
import uuid as _uuid
from typing import Any

from src.pipeline.source_constants import NewsSource


def brief(
    date: str,
    day_type: str = "normal",
    taptap_games: list[dict[str, Any]] | None = None,
    steam_ports: list[dict[str, Any]] | None = None,
    market_news: list[dict[str, Any]] | None = None,
    sector_changes: list[dict[str, Any]] | None = None,
    new_games_note: str = "",
    verbose: bool = False,
    warnings: list[str] | None = None,
    hot_topics_md: str = "",
    hot_items: list[dict[str, Any]] | None = None,
    yesterday_new_games: set[str] | None = None,
) -> dict[str, Any]:
    """Generate a Feishu card JSON for the daily report.

    Card structure (4 sections, assembled in code — no AI hallucination):
      1. 🆕 新游关注 — pre-built markdown (render.build_new_games_md)
      2. 📰 市场变动 — AI-written markdown (only part that touches AI)
      3. 📊 排名变动 — pre-built markdown (render.build_ranking_md)
      4. 🔥 热点追踪 — pre-built markdown (render.build_hot_topics_md)

    Args:
        date: Date string YYYY-MM-DD.
        day_type: quiet / normal / volatile.
        taptap_games: TapTap new games from DB (track-relevant, already filtered).
        steam_ports: Steam port games from DB.
        market_news: News headlines from DB (includes bilibili).
        sector_changes: Track-relevant rank changes.
        new_games_note: Optional note for the new games section.
        verbose: Print traces to stderr.
        warnings: Pipeline warnings for health summary.
        hot_topics_md: Hot topics section markdown (code-generated).
        hot_items: Hot topic news items for per-item click tracking buttons.

    Returns:
        Feishu card JSON dict with msg_type and card.
    """
    from src.agents.render import build_new_games_md, build_ranking_md, build_market_elements
    from src.agents.render import build_hot_topics_md, build_hot_topic_elements
    from src.agents.market_pipeline import filter_news, apply_fatigue, deep_fetch
    from src.agents.scorer import ai_summarize_and_judge
    from src.agents.dedup import (
        save_reported_news, headline_dedup_tokens,
    )

    # ── Build new games + ranking markdown in code (zero AI) ──
    new_games_md = build_new_games_md(
        steam_ports or [], taptap_games or [],
        new_games_note,
    )
    ranking_md = build_ranking_md(sector_changes or [], yesterday_new_games=yesterday_new_games)

    # ── News pipeline: hard filter → fatigue check → deep fetch → AI summarize ──
    # Phase A: hard filters only (block kw + dedup + freshness + track ignored)
    # NOTE: Non-selected candidates are NOT saved as "seen" — they are
    # reconsidered alongside new candidates the next day. Only the final
    # published top-N items are marked as reported (news type, 30-day TTL).
    # Topic fatigue (apply_fatigue) handles cross-day repetition control.
    candidates = filter_news(market_news or [], target_date=date)

    # Phase A2: fatigue check — downgrade/block topics seen in recent reports
    candidates = apply_fatigue(candidates, date)

    # Phase B: deep fetch article bodies for richer summaries
    enriched = deep_fetch(candidates)

    # Phase C+D: AI batch summarize + score + select top N (N from YAML config)
    top_news = ai_summarize_and_judge(enriched, date=date,
                                      day_type=day_type, verbose=verbose)

    # Mark selected news URLs + headline tokens as reported for long-term dedup
    pushed_urls = {n.get("url", "") for n in top_news if n.get("url")}
    pushed_tokens: set[str] = set()
    for n in top_news:
        pushed_tokens |= headline_dedup_tokens(n.get("headline", ""))
    if pushed_urls or pushed_tokens:
        save_reported_news(pushed_urls, date, headline_tokens=pushed_tokens)

    # ── Persist AI-annotated labels for Calibrator cross-analysis ──
    # pos_label / neg_label are selected from the Matched Verdict Pool
    # by the summarizer AI.  Writing them back to market_news lets the
    # Calibrator answer "do users preferentially 👍 items labeled
    # track_direct?" — which is the whole point of the label taxonomy.
    if top_news:
        try:
            from src.storage.sqlite import get_db
            url_labels: dict[str, tuple[str, str]] = {}
            for n in top_news:
                url = (n.get("url", "") or "").strip()
                pos = (n.get("pos_label", "") or "").strip()
                neg = (n.get("neg_label", "") or "").strip()
                if url and (pos or neg):
                    url_labels[url] = (pos, neg)
            if url_labels:
                updated = get_db().update_market_news_labels(date, url_labels)
                if verbose:
                    print(f"   [labels] persisted {updated} label annotations to market_news",
                          file=sys.stderr)
        except Exception as e:
            print(f"  [WARN] label persistence failed: {e}", file=sys.stderr)
            # best-effort — never block card generation on label persistence

    # Phase E: code-generated market section markdown (zero AI, deterministic).
    # The briefer LLM previously handled this, but it sometimes invented
    # placeholder URLs (example.com, wenku.so.com) that failed audit.
    # Since ai_summary is already written by the summarizer, the only
    # remaining work is arranging pre-written blurbs into markdown — trivial
    # to do in code, impossible to get wrong.
    if top_news:
        # Track-relevant first, then by ai_score descending
        sorted_news = sorted(top_news, key=lambda n: (
            not n.get("track_relevant", False),
            -(n.get("ai_score", 0)),
        ))
        md_blocks: list[str] = []
        for n in sorted_news:
            headline = n.get("headline", "") or ""
            source = n.get("source", "") or ""
            summary = (n.get("ai_summary", "") or "").strip()
            url = (n.get("url", "") or "").strip()
            # Build the markdown block.  Omit the link line when url is
            # genuinely empty so the card doesn't show [原文]().
            block = f"> **{headline}** — {source}\n> {summary}"
            if url:
                block += f"\n> → [原文]({url})"
            md_blocks.append(block)
        market_md = "\n\n".join(md_blocks)
        run_id = _uuid.uuid4().hex[:12]
    else:
        market_md = "> 今日无相关市场变动新闻。"
        run_id = _uuid.uuid4().hex[:12]

    # ── Assemble card JSON in code (deterministic) ──
    elements: list[dict[str, Any]] = []

    # Section 1: 新游关注
    elements.append({
        "tag": "markdown",
        "content": f"**🆕 新游关注**\n{new_games_md}",
    })

    # Section 2: 市场变动 — per-item blocks with per-item images + feedback buttons
    if not market_md.startswith("**"):
        market_md = f"**📰 市场变动**\n\n{market_md}"
    market_elements = build_market_elements(market_md, top_news, date=date)
    elements.extend(market_elements)

    # Section 3: 排名变动
    elements.append({
        "tag": "markdown",
        "content": f"**📊 排名变动**\n{ranking_md}",
    })

    # Section 4: 热点追踪 — code-generated markdown + per-item click tracking
    hot_topics_json_str = ""
    if hot_topics_md and hot_items:
        hot_elements = build_hot_topic_elements(hot_topics_md, hot_items, date=date)
        elements.extend(hot_elements)
        hot_topics_json_str = json.dumps(hot_items, ensure_ascii=False)
    elif hot_topics_md:
        elements.append({
            "tag": "markdown",
            "content": f"**🔥 热点追踪**\n\n{hot_topics_md}",
        })

    # Footer note
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text",
             "content": "💡 @我 追问任何产品/在研公司/赛道方向，我会做深度调研"}
        ],
    })

    # ── Health summary (only shown when there are warnings) ──
    if warnings and len(warnings) > 0:
        health_lines = ["⚠️ 系统状态"]
        for w in warnings[:5]:
            health_lines.append(f"  · {w[:120]}")
        if len(warnings) > 5:
            health_lines.append(f"  · … 及其他 {len(warnings) - 5} 条警告")
        elements.append({
            "tag": "markdown",
            "content": "\n".join(health_lines),
        })
    elif warnings is not None:
        # No warnings — show healthy status
        elements.append({
            "tag": "markdown",
            "content": "✅ 今日全部正常",
        })

    card_data = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"🎮 竞品情报日报 | {date}"},
                "template": "blue",
            },
            "elements": elements,
        },
    }

    # ── Persist to DB ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        brief_json = json.dumps(card_data, ensure_ascii=False)
        db.upsert_analysis_report(
            date=date,
            brief_card_json=brief_json,
            new_games_md=new_games_md,
            market_md=market_md,
            ranking_md=ranking_md,
            hot_topics_json=hot_topics_json_str,
        )
    except Exception as e:
        print(f"  [WARN] Failed to save report to DB: {e}", file=sys.stderr)

    card_data["_run_id"] = run_id
    return card_data


def brief_from_db(date: str, verbose: bool = False, warnings: list[str] | None = None) -> dict[str, Any]:
    """Run Briefer using all data already in the database.

    Reads from: taptap_new_games, steam_port_games, market_news,
    bilibili_videos, changes.

    Args:
        date: Date string YYYY-MM-DD.
        verbose: Print traces to stderr.

    Returns:
        Feishu card JSON dict.
    """
    from src.storage.sqlite import get_db
    from src.pipeline.differ import classify_day
    from src.pipeline.track_filter import filter_track_changes
    from src.agents.dedup import (
        load_reported_steam, save_reported_steam,
        load_reported_taptap, save_reported_taptap,
    )
    from src.agents.render import _parse_downloads, build_hot_topics_md

    db = get_db()

    # ── Determine day type from changes ──
    changes = db.get_changes_by_date(date)
    if changes:
        # total = all ranked games today (denominator for volatility),
        # NOT len(changes) — that would make volatility always 1.0.
        total_ranked = db._connect().execute(
            "SELECT COUNT(*) FROM rankings WHERE date = ?", (date,)
        ).fetchone()[0]
        up = down = new_entry = dropped_out = big_moves = 0
        for c in changes:
            ct = c.get("change_type", "")
            if ct == "up":
                up += 1
            elif ct == "down":
                down += 1
            elif ct == "new_entry":
                new_entry += 1
            elif ct == "dropped_out":
                dropped_out += 1
            if abs(c.get("rank_change") or 0) >= 15:
                big_moves += 1
        day_type = classify_day(
            total=total_ranked,
            up=up, down=down,
            new_entry=new_entry, dropped_out=dropped_out,
            big_moves=big_moves,
        )
    else:
        day_type = "normal"

    # ── Scraper data (直读 DB，不经 AI) ──
    taptap_all = db.get_taptap_games_by_date(date)
    steam_ports = db.get_steam_ports_by_date(date)
    market_news = db.get_market_news_by_date(date)

    # ── Steam port dedup ──
    reported_steam = load_reported_steam(date)
    steam_fresh = [s for s in (steam_ports or [])
                   if s.get("game_name", "") not in reported_steam]

    # ── TapTap dedup + split track vs new ──
    reported_tap = load_reported_taptap(date)
    taptap_fresh = [g for g in taptap_all if g.get("game_name", "") not in reported_tap]
    taptap_track = [g for g in taptap_fresh if g.get("track_relevant")]
    taptap_other = [g for g in taptap_fresh if not g.get("track_relevant")]

    # Selection logic for new games section
    if taptap_track:
        taptap_for_card = taptap_track
        new_games_note = ""
    else:
        # No track games — fall back to popular non-track games
        taptap_other.sort(key=lambda g: _parse_downloads(g.get("downloads", "")),
                          reverse=True)
        taptap_for_card = taptap_other[:5]
        new_games_note = "今日无赛道相关新游，以下是其他新上线游戏："

    # ── Mark reported (only games actually shown in the card) ──
    shown_steam_names = {s.get("game_name", "") for s in steam_fresh}
    shown_tap_names = {g.get("game_name", "") for g in taptap_for_card}
    if shown_steam_names:
        save_reported_steam(shown_steam_names, date)
    if shown_tap_names:
        save_reported_taptap(shown_tap_names, date)

    # ── Bilibili creator videos (merge into market_news) ──
    bilibili_videos = db.get_bilibili_videos_by_date(date)
    if bilibili_videos:
        bilibili_news = _bilibili_to_news(bilibili_videos)
        market_news = list(market_news) + bilibili_news

    # ── Pipeline data ──
    sector_changes = list(filter_track_changes(changes))

    # ── Yesterday's new games (for ranking table badge + force-include) ──
    from datetime import date as dt_date, timedelta
    yesterday_str = (dt_date.fromisoformat(date) - timedelta(days=1)).isoformat()
    yesterday_new_games = _yesterday_shown_games(db, yesterday_str)

    # Always include yesterday's new games in ranking, even if not track-relevant.
    # Prepend them so they survive the [:12] slice in build_ranking_md() — force-
    # included games must appear before any track-filtered entries get trimmed.
    if yesterday_new_games:
        from src.tools.taptap_resolver import fuzzy_match_game_name
        existing_names = {c.get("game_name", "") for c in sector_changes}
        extras: list[dict[str, Any]] = []
        for c in changes:
            name = c.get("game_name", "")
            if name not in existing_names and fuzzy_match_game_name(name, yesterday_new_games):
                extras.append(c)
                existing_names.add(name)
        sector_changes = extras + sector_changes

    # ── Hot topics data ──
    hot_items = db.get_hot_topic_news_by_date(date, selected=True, limit=7)
    hot_keywords_rows = db.get_hot_keywords_by_date(date)
    hot_keywords = [row.get("keyword", "") for row in hot_keywords_rows if row.get("keyword")]
    hot_topics_md = build_hot_topics_md(hot_items, hot_keywords) if hot_items else ""

    return brief(
        date=date,
        day_type=day_type,
        taptap_games=taptap_for_card,
        steam_ports=steam_fresh,
        market_news=market_news,
        sector_changes=sector_changes,
        new_games_note=new_games_note,
        verbose=verbose,
        warnings=warnings,
        hot_topics_md=hot_topics_md,
        hot_items=hot_items,
        yesterday_new_games=yesterday_new_games,
    )


# ═════════════════════════════════════════════════════════════
# Yesterday's shown games — for ranking-table badge
# ═════════════════════════════════════════════════════════════

def _yesterday_shown_games(db, yesterday_str: str) -> set[str]:
    """Return game names shown in yesterday's new-games card section.

    Replicates the selection logic from brief_from_db() for a past date:
      - TapTap: track-relevant games, or top-5 non-track by downloads
      - Steam: all ports

    Does NOT replicate cross-day dedup (can't reconstruct past dedup state),
    so this is a slight over-approximation — acceptable for the badge.
    """
    from src.agents.render import _parse_downloads

    shown: set[str] = set()

    # TapTap selection
    taptap_all = db.get_taptap_games_by_date(yesterday_str)
    taptap_track = [g for g in taptap_all if g.get("track_relevant")]
    if taptap_track:
        shown.update(g.get("game_name", "") for g in taptap_track)
    else:
        taptap_other = [g for g in taptap_all if not g.get("track_relevant")]
        taptap_other.sort(key=lambda g: _parse_downloads(g.get("downloads", "")),
                          reverse=True)
        shown.update(g.get("game_name", "") for g in taptap_other[:5])

    # Steam ports: all are shown in the card
    steam_all = db.get_steam_ports_by_date(yesterday_str)
    shown.update(s.get("game_name", "") for s in (steam_all or []))

    return shown


# ═════════════════════════════════════════════════════════════
# Data conversion: B站 videos → news-compatible format
# ═════════════════════════════════════════════════════════════

def _bilibili_to_news(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert bilibili_videos rows into market_news-compatible format.

    Runs track_filter on each video title to determine relevance.
    """
    try:
        from src.pipeline.track_filter import classify_game
    except Exception as e:
        print(f"  [WARN] track_filter import failed, B站 videos will not be classified: {e}", file=sys.stderr)
        classify_game = None

    news_items = []
    for v in videos:
        label = v.get("creator_label", "")
        title = v.get("title", "")
        tags_str = v.get("tags", "")
        subtitle = v.get("ai_subtitle", "")

        # Run track_filter on title + tags + subtitle
        track = False
        if classify_game:
            try:
                tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
                result = classify_game(
                    game_name=title,
                    tags=tag_list if tag_list else None,
                    description=subtitle[:500] if subtitle else "",
                )
                track = result == "track"
            except Exception as e:
                print(f"  [WARN] track_filter.classify_game failed for '{title[:40]}': {e}", file=sys.stderr)

        headline = f"[B站·{label}] {title}"
        news_items.append({
            "headline": headline,
            "source": NewsSource.BILIBILI,
            "url": v.get("url", ""),
            "track_relevant": track,
            "body": subtitle,  # AI字幕全文 → summarizer用
            "image_url": v.get("cover", ""),
        })
    return news_items


# ═════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════

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
