#!/usr/bin/env python3
"""
Feedback-weighted news scorer — read ALL today's news from DB (market_news +
bilibili_videos), score with the summarizer AI, and output the top N.

Covers all 7 sources: 17173 / 3DM / 游戏陀螺 / 游戏日报 / GameLook /
PocketGamer.biz / Bilibili (creator videos with AI subtitles).

Usage:
    python scripts/score_news.py                     # today
    python scripts/score_news.py --top 12            # override top N
    python scripts/score_news.py --date 2026-06-24   # specific date
    python scripts/score_news.py --batch-size 15     # items per LLM call
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.agents.base import Agent
from src.agents.scorer import SummarizerOutput, build_feedback_summary, _extract_body_signals
from src.pipeline.source_constants import normalize_source, source_order, source_label
from src.pipeline.token_utils import extract_game_names, extract_topic_words
from src.storage.sqlite import get_db


def load_news_db(date: str) -> list[dict[str, Any]]:
    """Load all news from DB for a given date (market_news + bilibili_videos).

    The two queries are independent — run them in parallel to cut wall-clock I/O.
    """
    db = get_db()

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_news = ex.submit(db.get_market_news_by_date, date)
        fut_videos = ex.submit(db.get_bilibili_videos_by_date, date)
        news_items = fut_news.result()
        videos = fut_videos.result()

    rows: list[dict[str, Any]] = []

    # ── market_news: 5 Chinese sources + PG.biz ──
    for r in news_items:
        src = r.get("source", "?")
        label = source_label(src)
        rows.append({
            "id": len(rows),
            "headline": r.get("headline", ""),
            "source": label,
            "url": r.get("url", ""),
            "track_relevant": bool(r.get("track_relevant")),
            "body": "",
        })

    # ── bilibili_videos: creator videos with AI subtitles ──
    for v in videos:
        creator = v.get("creator_label", "B站")
        label = f"B站·{creator}"
        title = v.get("title", "")
        desc = (v.get("ai_subtitle") or v.get("description") or "")[:500]
        rows.append({
            "id": len(rows),
            "headline": f"[B站·{creator}] {title}",
            "source": label,
            "url": v.get("url", ""),
            "track_relevant": False,  # bilibili videos are manually curated, not keyword-matched
            "body": desc,
        })

    return rows


# ── Same-day content dedup ──

# Source authority ranking for tie-breaking duplicates (lower = higher priority)
# Delegated to source_constants.source_order()


def _extract_content_tokens(headline: str) -> tuple[set[str], set[str]]:
    """Extract (game_names, topic_words) from a headline.

    Delegates to src.pipeline.token_utils for core extraction logic.
    """
    games = extract_game_names(headline)

    # Remove game names from headline before extracting topic words
    clean = headline
    for g in games:
        clean = clean.replace(f"《{g}》", "")

    noise = {
        "steam", "game", "play", "app", "报道", "文章", "分析",
        "认为", "表示", "显示", "数据", "目前", "已经", "可以",
        "进行", "一个", "这是", "这个", "其中", "通过", "以及",
        "包括", "对于", "根据", "作为", "不仅", "同时", "此外",
        "游戏", "玩家", "近日", "本周", "最新", "发布", "推出",
        "正式", "公布", "曝光", "介绍", "据悉", "了解",
        "陀螺周报", "独家", "资讯", "头条",
    }
    topic_words = extract_topic_words(clean, min_chinese=2, min_english=3, noise=noise)

    return games, topic_words


def dedup_same_day(
    candidates: list[dict[str, Any]],
    verbose: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    """Remove same-day duplicate coverage of the same event.

    Two articles are considered duplicates when they:
      1. Share at least one game name (from 《》), AND
      2. Share at least 2 topic words (significant content overlap).

    Among duplicates, the article from the higher-authority source wins.
    """
    n = len(candidates)

    # ── Step 1: Extract features for each candidate ──
    features: list[tuple[set[str], set[str]]] = []
    for c in candidates:
        games, topics = _extract_content_tokens(c.get("headline", ""))
        features.append((games, topics))

    # ── Step 2: Build game-name index ──
    game_index: dict[str, list[int]] = {}
    for i, (games, _) in enumerate(features):
        for g in games:
            game_index.setdefault(g, []).append(i)

    # ── Step 3: Find duplicates within each game group ──
    dup_indices: set[int] = set()
    dup_details: list[str] = []

    for game, indices in game_index.items():
        if len(indices) <= 1:
            continue
        for a in range(len(indices)):
            for b in range(a + 1, len(indices)):
                i, j = indices[a], indices[b]
                if i in dup_indices or j in dup_indices:
                    continue

                games_i, topics_i = features[i]
                games_j, topics_j = features[j]

                # Shared game name(s) already guaranteed by the index.
                # Check topic word overlap as the "same event" signal.
                shared_topics = topics_i & topics_j
                if len(shared_topics) < 2:
                    continue

                # ── Same event detected → keep higher authority source ──
                src_i = candidates[i].get("source", "")
                src_j = candidates[j].get("source", "")
                pri_i = source_order(src_i)
                pri_j = source_order(src_j)

                if pri_i <= pri_j:
                    dup_indices.add(j)
                    winner, loser = i, j
                else:
                    dup_indices.add(i)
                    winner, loser = j, i

                h_win = candidates[winner].get("headline", "")[:50]
                h_los = candidates[loser].get("headline", "")[:50]
                dup_details.append(
                    f"  ✂️  [{src_i}]「{h_win}」⊃ [{src_j}]「{h_los}」"
                    f"  (game={game}, overlap={shared_topics})"
                )

    # ── Step 4: Filter ──
    kept = [c for i, c in enumerate(candidates) if i not in dup_indices]
    removed = len(dup_indices)

    if verbose and dup_details:
        print(f"  Dedup: {removed} duplicates removed ({n} → {len(kept)})", file=sys.stderr)
        for detail in dup_details:
            print(detail, file=sys.stderr)

    return kept, removed


def batch_score(
    candidates: list[dict[str, Any]],
    date: str,
    batch_size: int = 15,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Score all candidates in batches using the summarizer agent."""
    feedback_summary = build_feedback_summary(days=14)

    agent = Agent(
        "summarizer",
        tools=None,
        model=None,
        max_tool_rounds=1,
        max_tokens=8192,
        output_schema=SummarizerOutput,
    )

    all_scored: list[dict[str, Any]] = []
    n_batches = (len(candidates) + batch_size - 1) // batch_size

    for b in range(n_batches):
        start = b * batch_size
        end = min(start + batch_size, len(candidates))
        batch = candidates[start:end]
        label = f"batch {b+1}/{n_batches} ({start+1}-{end})"

        items_json = json.dumps([
            {"id": i, "headline": c["headline"], "source": c["source"],
             "track_relevant": c.get("track_relevant", False),
             "body": (c.get("body", "") or "")[:500],
             "signals": _extract_body_signals(c.get("body", "") or "")}
            for i, c in enumerate(candidates[start:end])
        ], ensure_ascii=False, indent=2)

        if verbose:
            print(f"  [{label}] scoring {len(batch)} candidates...", file=sys.stderr)

        try:
            result = agent.run(
                date=date,
                day_type="normal",
                feedback_summary=feedback_summary,
                market_news_json=items_json,
                top_n=str(len(batch)),
                retry_context="",
                _verbose=False,
            )
        except Exception as e:
            print(f"  [WARN] {label} LLM call failed: {e}", file=sys.stderr)
            for i, c in enumerate(batch):
                item = dict(c)
                item["ai_summary"] = ""
                item["ai_score"] = 0
                item["ai_verdict"] = ""
                all_scored.append(item)
            continue

        raw = result.get("candidates", {})
        if result.get("_schema_errors"):
            print(f"   [warn] schema errors in {label}: {result['_schema_errors']}", file=sys.stderr)

        for i, c in enumerate(batch):
            item = dict(c)
            r = raw.get(str(i), {})
            item["ai_summary"] = r.get("summary", "") or ""
            item["ai_score"] = r.get("score", 0) or 0
            item["ai_verdict"] = r.get("verdict", "") or ""
            all_scored.append(item)

    return all_scored


def print_top(scored: list[dict[str, Any]], top_n: int = 12) -> None:
    """Print the top N news items with full scoring breakdown."""
    scored.sort(key=lambda x: x.get("ai_score", 0), reverse=True)
    top = scored[:top_n]

    print(f"\n{'='*80}")
    print(f"  🏆 Top {top_n} / {len(scored)} news (feedback-weighted AI scoring)")
    print(f"{'='*80}\n")

    for rank, item in enumerate(top, 1):
        tr = " 🏷️" if item.get("track_relevant") else ""
        src = item.get("source", "?")
        headline = item.get("headline", "?")
        summary = item.get("ai_summary", "")
        verdict = item.get("ai_verdict", "") or ""

        print(f"  {rank:>2}. [{src:16s}] {item['ai_score']:>3}分{tr}")
        print(f"      {verdict[:40]:40s} |  {headline[:70]}")
        if summary:
            for line in summary.split("\n"):
                print(f"      📝 {line.strip()[:100]}")
        print()

    # ── Source distribution ──
    sources: dict[str, int] = {}
    for item in top:
        s = item.get("source", "?")
        sources[s] = sources.get(s, 0) + 1
    print(f"  Source distribution: {sources}")

    # ── Feedback signal ──
    fd = build_feedback_summary(days=14)
    if fd and "暂无" not in fd:
        print(f"\n  Feedback data used:\n{fd}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score all news with feedback-weighted AI, output top N"
    )
    parser.add_argument("--date", type=str, default=None,
                        help="Date YYYY-MM-DD (default: today)")
    parser.add_argument("--top", type=int, default=12,
                        help="Number of top news to output (default: 12)")
    parser.add_argument("--batch-size", type=int, default=15,
                        help="Candidates per LLM call (default: 15)")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Skip same-day content dedup")
    args = parser.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.date:
        date = args.date
    else:
        date = datetime.now().strftime("%Y-%m-%d")

    print(f"  📰 Feedback-weighted News Scorer (DB)", file=sys.stderr)
    print(f"  Date: {date}  |  Top: {args.top}  |  Batch: {args.batch_size}", file=sys.stderr)

    # 1. Load from DB
    news = load_news_db(date)
    if not news:
        print("[ERROR] No news found in DB for this date.", file=sys.stderr)
        sys.exit(1)

    # 2. Source breakdown
    from collections import Counter
    src_counts = Counter(n["source"] for n in news)
    print(f"  Loaded {len(news)} items: {dict(src_counts)}", file=sys.stderr)

    # 2.5 Same-day content dedup (always on unless --no-dedup)
    if not args.no_dedup:
        news, removed = dedup_same_day(news)
        if removed:
            print(f"  Dedup: {removed} removed → {len(news)} remaining", file=sys.stderr)
    else:
        print(f"  Dedup: skipped (--no-dedup)", file=sys.stderr)

    # 3. Feedback summary
    fb = build_feedback_summary(days=14)
    has_fb = "暂无" not in fb
    print(f"  Feedback data: {'✅ ' + str(fb.count(chr(124))//4) + ' items' if has_fb else '❌ none'}", file=sys.stderr)

    # 4. Score all
    print(f"  Scoring {len(news)} candidates in batches of {args.batch_size}...\n", file=sys.stderr)
    scored = batch_score(news, date=date, batch_size=args.batch_size)

    # 5. Print top N
    print_top(scored, top_n=args.top)


if __name__ == "__main__":
    main()
