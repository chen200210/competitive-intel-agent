"""
AI summarizer + judge — batch-process news candidates through LLM scoring.

Phase C: AI batch summarizes all candidates that passed hard filters,
         scores each on a single 0-100 business-value scale (with anchored
         bands replacing the old 4-dimension academic rubric), then selects
         top N with source diversity constraints.

Includes zero-token body-signal extraction (body_len / fact_count /
freshness / is_digest) injected into the prompt so the AI doesn't waste
capacity on mechanical checks.  A distribution-compliance retry loop
with code-layer fallback prevents the "safe middle" scoring collapse.
"""

from __future__ import annotations

import functools
import json
import re
import sys
from typing import Any

from pydantic import BaseModel

from src.agents.base import Agent
from src.pipeline.source_constants import is_bilibili, is_overseas, NewsSource


# ═════════════════════════════════════════════════════════════
# Scoring config (loaded from competitor_list.yaml, with defaults)
# ═════════════════════════════════════════════════════════════

@functools.lru_cache(maxsize=1)
def load_scoring_config() -> dict[str, Any]:
    """Load scoring params from competitor_list.yaml track_config.scoring.

    Returns a dict with keys: min_ai_score, top_n, max_bilibili,
    max_per_source.  Hardcoded defaults are used when the YAML file
    is unavailable or the scoring section is absent.
    """
    defaults: dict[str, Any] = {
        "min_ai_score": 40,
        "top_n": 7,
        "max_bilibili": 2,
        "max_per_source": 3,
    }
    try:
        import yaml
        from src.config import settings
        yaml_path = settings.competitor_list_path
        if yaml_path.exists():
            with open(yaml_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            yaml_scoring = config.get("track_config", {}).get("scoring", {})
            if yaml_scoring:
                return {**defaults, **yaml_scoring}
    except Exception:
        pass
    return defaults


class NewsScore(BaseModel):
    """Per-candidate scoring from summarizer AI (total + labels + verdict)."""
    summary: str = ""
    score: int = 0             # 0-100 业务价值总分
    pos_label: str = ""        # 正面标签 (从 Matched Verdict Pool 选择)
    neg_label: str = ""        # 负面标签 (从 Matched Verdict Pool 选择)
    verdict: str = ""          # ≤20字判词，说明最短板或最亮点


# Matched Verdict Pool — must match summarizer.yaml (P2)
_VALID_POS_LABELS: frozenset[str] = frozenset({
    "track_direct", "playable_reference", "ai_related",
    "high_info_density", "exclusive_scoop", "overseas_insight",
})
_VALID_NEG_LABELS: frozenset[str] = frozenset({
    "no_gameplay", "digest_rerun", "off_track",
    "low_info", "stale", "generic_pr",
})


class SummarizerOutput(BaseModel):
    """Validated output from the summarizer AI."""
    candidates: dict[str, NewsScore]  # key = candidate index as string
    duplicates: list[list[int]] = []  # pairs of indices that are the same story, e.g. [[2,7], [3,5]]


# ═════════════════════════════════════════════════════════════
# Feedback summary for AI prompt
# ═════════════════════════════════════════════════════════════

def build_feedback_summary(days: int = 14) -> str:
    """Query recent user feedback and format a compact reference for the AI.

    Aggregates from user_feedback (source of truth) and joins with
    market_news across all dates for headline/source metadata.
    Also includes current calibration dim_weights when available.
    """
    from datetime import datetime, timedelta
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = db._connect().execute("""
            SELECT
                uf.news_url as url,
                SUM(CASE WHEN uf.feedback_type = 'thumbs_up' THEN 1 ELSE 0 END) as up,
                SUM(CASE WHEN uf.feedback_type = 'thumbs_down' THEN 1 ELSE 0 END) as down,
                mn.headline,
                mn.source
            FROM user_feedback uf
            LEFT JOIN (
                SELECT url, headline, source
                FROM market_news
                GROUP BY url
            ) mn ON mn.url = uf.news_url
            WHERE uf.date >= ?
            GROUP BY uf.news_url
            ORDER BY up DESC, down DESC
            LIMIT 30
        """, (cutoff,)).fetchall()

        # ── Calibration context (if available) ──
        calib_note = ""
        try:
            calib = db.get_latest_calibration_params()
            if calib:
                dw = calib.get("dim_weights", {})
                calib_note = (
                    f"\n当前校准维度权重 (v{calib.get('version', '?')}): "
                    f"赛道相关性={dw.get('track', 40)}, "
                    f"信息密度={dw.get('density', 40)}, "
                    f"行业洞察={dw.get('insight', 20)}"
                )
        except Exception:
            pass

        if not rows:
            return "（暂无用户反馈数据，按默认标准打分即可）" + calib_note

        lines = ["| 来源 | 有用 | 没用 | 新闻标题 |",
                 "| --- | --- | --- | --- |"]
        for r in rows:
            src = (r["source"] or "?")[:10]
            h = (r["headline"] or r["url"] or "?")[:40]
            lines.append(
                f"| {src} | {r['up']} | {r['down']} | {h} |"
            )
        return "\n".join(lines) + calib_note
    except Exception:
        return "（反馈数据读取失败，按默认标准打分即可）"


# ═════════════════════════════════════════════════════════════
# Body signal extraction (zero-token, regex only)
# ═════════════════════════════════════════════════════════════

# Companies matched in body text for fact_count
_KNOWN_COMPANIES: list[str] = [
    "腾讯", "网易", "米哈游", "字节跳动", "莉莉丝", "叠纸", "鹰角",
    "完美世界", "三七互娱", "索尼", "任天堂", "微软", "Steam",
    "Playtika", "Tencent", "NetEase", "miHoYo", "Garena",
    "育碧", "EA", "暴雪", "Riot", "Embracer", "Take-Two",
    "Nexon", "Netmarble", "Krafton", "Pearl Abyss",
    "凉屋", "祖龙", "快手", "B站", "哔哩哔哩",
]

# Time-marker priority: first match wins (ordered most→least specific)
_FRESHNESS_MARKERS: list[tuple[str, str]] = [
    ("今日", "今日"), ("今天", "今日"), ("今晚", "今日"),
    ("昨日", "昨日"), ("昨天", "昨日"),
    ("本周", "本周"), ("这周", "本周"),
    ("上周", "上周"),
    ("本月", "本月"),
    ("上月", "上月"),
]

_DIGEST_KEYWORDS: list[str] = [
    "周报", "汇总", "盘点", "总结", "周榜", "月报",
    "Weekly", "Roundup", "Digest",
]


def _extract_body_signals(body: str) -> dict[str, Any]:
    """Extract reference signals from body text for the AI prompt.

    All signals are computed with zero-token regex / string ops.
    Returns a compact dict injected into each candidate's JSON entry.
    """
    if not body:
        return {
            "body_len": 0,
            "fact_count": 0,
            "freshness": "无正文",
            "is_digest": False,
        }

    bl = len(body)
    if bl <= 100:
        body_len_label = "1-100"
    elif bl <= 500:
        body_len_label = "100-500"
    else:
        body_len_label = "500+"

    # ── fact_count: countable data points ──
    fc = 0
    # Numbers with units (金额 / 百分比 / 万/亿 scaled)
    fc += len(re.findall(r'\d+\.?\d*[万亿千百]?[美元港元%亿]?', body))
    # Game names in 《》
    fc += len(re.findall(r'《[^》]+》', body))
    # Known company mentions
    for co in _KNOWN_COMPANIES:
        if co in body:
            fc += 1
    fact_count = min(fc, 20)  # cap to keep prompt compact

    # ── freshness: most specific time marker ──
    freshness = "未标注"
    for marker, label in _FRESHNESS_MARKERS:
        if marker in body:
            freshness = label
            break
    if freshness == "未标注":
        m = re.search(r'(\d+)月', body)
        if m:
            freshness = f"{m.group(1)}月"
        elif re.search(r'去年', body):
            freshness = "去年或更早"
        else:
            # Match any 4-digit year and flag as stale if before current year
            ym = re.search(r'(?:^|\D)(\d{4})\s*年', body)
            if ym:
                from datetime import datetime
                try:
                    yr = int(ym.group(1))
                    if yr < datetime.now().year:
                        freshness = "去年或更早"
                except ValueError:
                    pass

    # ── is_digest ──
    is_digest = any(kw in body for kw in _DIGEST_KEYWORDS)

    return {
        "body_len": body_len_label,
        "fact_count": fact_count,
        "freshness": freshness,
        "is_digest": is_digest,
    }


# ═════════════════════════════════════════════════════════════
# β-Fusion: code-layer signal score (P1, RecGPT pattern 3)
# ═════════════════════════════════════════════════════════════

# Weight of code-layer signal in final fused score.
# 0.3 = AI dominates (70%) but code signal has meaningful pull.
# RecGPT used β=0.5 for recommendation; our task is more semantic,
# so AI gets more weight.  0.3 is enough to prevent LLM from
# scoring a 500-word data-rich article at 25 or an empty-digest at 75.
_BETA = 0.3

# Hard floor: when signal_score falls below this, AI scores above
# _AI_SOFT_CAP are clamped.  Prevents "hollow but AI loved it" outliers.
_SIGNAL_FLOOR = 20
_AI_SOFT_CAP = 60


def _compute_signal_score(signals: dict[str, Any]) -> float:
    """Compute a 0-100 reference score from body signals (zero token, regex only).

    Designed to be on the same scale as AI scores so β-fusion is meaningful.
    Baseline is 40 (neutral) — same center as the AI scoring bands.
    """
    score = 40.0

    # ── body_len: short body = weak signal ──
    bl = signals.get("body_len", 0)
    if bl in (0, "1-100"):
        score -= 15          # no/fragment body → likely low info
    elif bl == "500+":
        score += 5           # rich body → slight boost

    # ── fact_count: data points → information density ──
    fc = signals.get("fact_count", 0)
    if fc >= 5:
        score += 10          # data-rich
    elif fc >= 2:
        score += 5           # adequate
    elif fc == 0:
        score -= 10          # no facts detected

    # ── is_digest: secondary processing → not original reporting ──
    if signals.get("is_digest"):
        score -= 10

    # ── freshness: newer = better ──
    freshness = signals.get("freshness", "")
    if freshness in ("今日", "昨日"):
        score += 10
    elif freshness in ("上周", "上月", "去年或更早"):
        score -= 10

    return max(0.0, min(100.0, score))


# ═════════════════════════════════════════════════════════════
# Distribution compliance check + fallback
# ═════════════════════════════════════════════════════════════

def _check_distribution(scores: list[int], total: int) -> tuple[bool, str, dict[str, int]]:
    """Check whether AI scores satisfy forced-distribution requirements.

    Returns (is_ok, message, counts_dict).
    Skips check entirely when total < 4 (not enough items to judge).
    """
    if total < 4:
        return True, "条目过少(<4)，跳过分布检查", {"below_40": 0, "above_60": 0, "total": total}

    below_40 = sum(1 for s in scores if s < 40)
    above_60 = sum(1 for s in scores if s > 60)

    need_below = max(1, int(total * 0.25))
    max_above = int(total * 0.30)

    issues: list[str] = []
    if below_40 < need_below:
        issues.append(f"低于40分：需≥{need_below}条，实际{below_40}条")
    if above_60 > max_above:
        issues.append(f"高于60分：需≤{max_above}条，实际{above_60}条")

    counts = {"below_40": below_40, "above_60": above_60, "total": total}
    if issues:
        return False, "；".join(issues), counts
    return True, "分布合格", counts


def _apply_score_fallback(all_items: dict[int, dict[str, Any]], total: int) -> None:
    """Code-layer distribution enforcement when AI fails after all retries.

    Sorts by current ai_score, then caps outliers:
      - Bottom 25% → max 39
      - Top 30%  → max 64

    Preserves relative ordering within bands.  Original score is saved to
    ``_raw_score`` for audit logging.
    """
    if total < 4:
        return

    need_below = max(1, int(total * 0.25))
    max_above = int(total * 0.30)

    sorted_items = sorted(all_items.items(), key=lambda x: x[1].get("ai_score", 0))

    # ── Bottom band: cap at 39 ──
    for _, item in sorted_items[:need_below]:
        if item.get("ai_score", 0) >= 40:
            item["_raw_score"] = item["ai_score"]
            item["ai_score"] = 39

    # ── Top band: cap at 64 ──
    # max_above is the number of items allowed to exceed 60.
    # When max_above=0 (e.g. total=3, 3*0.30=0), NO items may exceed 60,
    # so we cap every item with score > 65 regardless of position.
    if max_above > 0:
        top_start = len(sorted_items) - max_above
        top_band = sorted_items[top_start:]
    else:
        top_band = sorted_items  # cap ALL items if zero slots allowed >60
    for _, item in top_band:
        if item.get("ai_score", 0) > 65:
            if "_raw_score" not in item:
                item["_raw_score"] = item["ai_score"]
            item["ai_score"] = 64


# ═════════════════════════════════════════════════════════════
# AI batch summarize + judge → top N
# ═════════════════════════════════════════════════════════════

def _fmt_body(body: str, fatigue: str) -> str:
    """Format body for AI prompt, prepending fatigue note when applicable."""
    if fatigue == "downgraded":
        prefix = "[注意：此话题昨日已推送过，如无重大更新可降低推荐优先级]\n"
        return prefix + body
    return body


# Max attempts for distribution-compliance retry (outside Agent's own
# schema-validation retries handled in base.py).
_MAX_DIST_RETRIES = 3


def ai_summarize_and_judge(
    candidates: list[dict[str, Any]],
    date: str,
    top_n: int | None = None,
    day_type: str = "normal",
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Batch AI: summarize each candidate, score on 0-100 scale, select top N.

    Returns the selected items with ai_summary, ai_score, and ai_verdict
    fields.  Includes a distribution-compliance retry loop so the AI cannot
    collapse all scores into a narrow "safe middle" band.

    Scoring parameters (min_ai_score, top_n) are read from
    competitor_list.yaml → track_config.scoring.  Pass top_n explicitly
    to override the YAML value.
    """
    if not candidates:
        return []

    cfg = load_scoring_config()
    min_ai_score: int = cfg["min_ai_score"]
    if top_n is None:
        top_n = cfg["top_n"]
    max_bilibili: int = cfg["max_bilibili"]

    # ── Extract body signals for each candidate (zero token) ──
    items_json = json.dumps([
        {
            "id": i,
            "headline": c.get("headline", ""),
            "source": c.get("source", ""),
            "track_relevant": c.get("track_relevant", False),
            "body": (fb := _fmt_body(c.get("body", "") or "", c.get("fatigue", "")))[:500],
            "signals": _extract_body_signals(fb),
        }
        for i, c in enumerate(candidates)
    ], ensure_ascii=False, indent=2)

    feedback_summary = build_feedback_summary()

    # ═══════════════════════════════════════════════════════
    # Retry loop: enforce forced distribution
    # ═══════════════════════════════════════════════════════
    retry_context = ""
    raw_candidates: dict[str, Any] = {}
    dist_ok = False  # set True when distribution check passes

    for attempt in range(_MAX_DIST_RETRIES):
        agent = Agent(
            "summarizer",
            tools=None,
            model=None,
            max_tool_rounds=1,
            max_tokens=16384,
            output_schema=SummarizerOutput,
        )

        try:
            result = agent.run(
                date=date,
                day_type=day_type,
                feedback_summary=feedback_summary,
                market_news_json=items_json,
                retry_context=retry_context,
                _verbose=False,  # keep agent chatter quiet; we print our own table
            )
        except Exception:
            if attempt < _MAX_DIST_RETRIES - 1:
                retry_context = "\n⚠️ 上一次调用失败，请重新输出完整 JSON。\n"
                continue
            # Last attempt failed entirely — fall through to fallback
            result = {}

        raw_candidates = result.get("candidates") or {}
        if result.get("_schema_errors"):
            print(
                f"   [warn] summarizer schema errors (attempt {attempt+1}): "
                f"{result['_schema_errors']}",
                file=sys.stderr,
            )

        # Extract all scores for distribution check
        scores: list[int] = []
        for i in range(len(candidates)):
            raw = raw_candidates.get(str(i), {})
            try:
                scores.append(int(raw.get("score", 0) or 0))
            except (ValueError, TypeError):
                scores.append(0)

        dist_ok, dist_msg, dist_counts = _check_distribution(scores, len(candidates))
        if verbose:
            print(
                f"   [dist] attempt {attempt+1}: {dist_msg}",
                file=sys.stderr,
            )

        if dist_ok:
            break

        if attempt < _MAX_DIST_RETRIES - 1:
            need_below = max(1, int(len(candidates) * 0.25))
            max_above = int(len(candidates) * 0.30)   # must match _check_distribution
            retry_context = (
                f"\n⚠️ 上一次打分分布不合格：{dist_msg}\n"
                f"低于40分的需要 ≥{need_below} 条（实际 {dist_counts['below_40']} 条）。\n"
                f"高于60分的最多 ≤{max_above} 条（实际 {dist_counts['above_60']} 条）。\n"
                f"请重新打分，务必拉开差距，不要都挤在中间段。\n"
            )
    else:
        # All retries exhausted — code-layer fallback
        if verbose:
            print("   [dist] 分布重试耗尽，启用代码层兜底修正", file=sys.stderr)

    # ── Complete AI failure fallback: if no candidates were returned at all,
    #     return top N items with default fields so downstream doesn't break ──
    if not raw_candidates:
        if verbose:
            print("   [warn] AI returned no candidates after all retries, using fallback", file=sys.stderr)
        fallback: list[dict[str, Any]] = []
        for c in candidates[:top_n]:
            item = dict(c)
            item.setdefault("ai_summary", "")
            item.setdefault("ai_score", 0)
            item.setdefault("ai_verdict", "")
            item.setdefault("pos_label", "")
            item.setdefault("neg_label", "")
            fallback.append(item)
        return fallback

    # ── Build all_items from (possibly fallback-adjusted) raw_candidates ──
    all_items: dict[int, dict[str, Any]] = {}  # orig_idx → scored item
    for i, c in enumerate(candidates):
        item = dict(c)
        item["_orig_idx"] = i
        raw = raw_candidates.get(str(i), {})
        item["ai_summary"] = raw.get("summary", "") or ""
        try:
            item["ai_score"] = int(raw.get("score", 0) or 0)
        except (ValueError, TypeError):
            item["ai_score"] = 0
        item["pos_label"] = raw.get("pos_label", "") or ""
        item["neg_label"] = raw.get("neg_label", "") or ""
        # Validate against Matched Verdict Pool — drop hallucinated labels
        if item["pos_label"] and item["pos_label"] not in _VALID_POS_LABELS:
            item["pos_label"] = ""
        if item["neg_label"] and item["neg_label"] not in _VALID_NEG_LABELS:
            item["neg_label"] = ""
        item["ai_verdict"] = raw.get("verdict", "") or ""

        # ── β-Fusion: blend code-layer signal_score with AI score ──
        # Runs BEFORE distribution check — spreads scores naturally,
        # reducing retry frequency.  signal_score is zero-token (regex).
        body_for_signals = _fmt_body(c.get("body", "") or "", c.get("fatigue", ""))
        signals = _extract_body_signals(body_for_signals)
        signal_score = _compute_signal_score(signals)
        item["signal_score"] = int(signal_score)

        ai_s = item["ai_score"]
        fused = _BETA * signal_score + (1 - _BETA) * ai_s

        # Hard constraint: signal says "junk" but AI says "great" → cap
        if signal_score < _SIGNAL_FLOOR and ai_s > _AI_SOFT_CAP:
            fused = min(fused, _AI_SOFT_CAP - 1)  # 59

        item["ai_score"] = int(round(fused))
        all_items[i] = item

    # ── Calibration: apply topic_boosts from Calibrator agent ──
    # Runs BEFORE distribution enforcement so topic boosts cannot
    # silently undo the fallback caps (Angle G finding 1).
    try:
        from src.agents.calibrator import load_calibration_for_scorer, apply_topic_boosts
        calib = load_calibration_for_scorer()
        topic_boosts = calib.get("topic_boosts", {})
        if topic_boosts:
            for item in all_items.values():
                headline = item.get("headline", "")
                raw_score = item.get("ai_score", 0)
                adjusted = apply_topic_boosts(headline, raw_score, topic_boosts)
                if adjusted != raw_score:
                    item["_calib_raw_score"] = raw_score
                    item["ai_score"] = adjusted
            if verbose:
                boosted = sum(1 for item in all_items.values()
                             if "_calib_raw_score" in item)
                if boosted:
                    print(
                        f"   [calib] topic_boosts applied to {boosted} items "
                        f"(calibration v{calib.get('version', 0)})",
                        file=sys.stderr,
                    )
    except Exception:
        pass  # best-effort — never block scoring on calibration error

    # ── Distribution enforcement: FINAL guard after all score adjustments ──
    # β-fusion and topic_boosts have already spread scores.  Only apply
    # fallback if distribution is still non-compliant.
    scores_after_adjustments = [item["ai_score"] for item in all_items.values()]
    dist_ok, dist_msg, _ = _check_distribution(scores_after_adjustments, len(candidates))
    if not dist_ok:
        if verbose:
            print(f"   [dist] post-adjustment distribution needs correction: {dist_msg}",
                  file=sys.stderr)
        _apply_score_fallback(all_items, len(candidates))

    # ── AI-flagged duplicates: compute connected components, keep only the
    #     highest-scoring item per component ──
    removed: set[int] = set()  # ids of items to drop
    try:
        ai_duplicates: list[list[int]] = result.get("duplicates") or []
        if ai_duplicates:
            # Build adjacency graph from validated pairs
            graph: dict[int, set[int]] = {}
            for pair in ai_duplicates:
                if not isinstance(pair, (list, tuple)):
                    continue
                if len(pair) < 2:
                    continue
                a, b = pair[0], pair[1]
                if not (isinstance(a, int) and not isinstance(a, bool)
                        and isinstance(b, int) and not isinstance(b, bool)):
                    continue
                if a == b:
                    continue
                graph.setdefault(a, set()).add(b)
                graph.setdefault(b, set()).add(a)

            # Find connected components (DFS)
            seen: set[int] = set()
            for node in graph:
                if node in seen:
                    continue
                comp: set[int] = set()
                stack = [node]
                while stack:
                    n = stack.pop()
                    if n in seen:
                        continue
                    seen.add(n)
                    comp.add(n)
                    stack.extend(graph.get(n, set()) - seen)
                if len(comp) <= 1:
                    continue
                # Keep the highest-scoring item in this component
                items_in_comp = [
                    (idx, all_items[idx]) for idx in comp if idx in all_items
                ]
                if len(items_in_comp) <= 1:
                    continue
                items_in_comp.sort(key=lambda x: x[1]["ai_score"], reverse=True)
                # Drop all but the winner
                for idx, item in items_in_comp[1:]:
                    removed.add(id(item))
                    if verbose:
                        print(
                            f"   [dedup] duplicate of #{items_in_comp[0][0]} — "
                            f"dropping #{idx} ({item['ai_score']} pts): "
                            f"{item['headline'][:50]}",
                            file=sys.stderr,
                        )
    except Exception:
        print(
            f"   [warn] dedup failed, proceeding without dedup",
            file=sys.stderr,
        )

    # ── Quality gate + dedup removal ──
    all_scored: list[dict[str, Any]] = [
        item for idx, item in all_items.items()
        if item["ai_score"] >= min_ai_score and id(item) not in removed
    ]

    # Sort by AI score descending, then apply source diversity
    all_scored.sort(key=lambda x: x.get("ai_score", 0), reverse=True)

    # ── Verbose: print full scoring table ──
    if verbose:
        print("\n── AI 打分明细 ──", file=sys.stderr)
        for idx, item in enumerate(all_scored):
            tr = "🏷️" if item.get("track_relevant") else "  "
            raw_note = ""
            if item.get("_raw_score") is not None:
                raw_note = f" (raw:{item['_raw_score']})"
            sig = item.get("signal_score", 0)
            calib_raw = item.get("_calib_raw_score")
            calib_note = f" calib:{calib_raw}→{item['ai_score']}" if calib_raw else ""
            pos = item.get("pos_label", "")
            neg = item.get("neg_label", "")
            label_str = ""
            if pos or neg:
                label_str = f" [{pos}][{neg}]" if neg else f" [{pos}]"
            verdict = item.get("ai_verdict", "") or ""
            print(
                f"  {idx+1:>2}. {tr} {item['ai_score']:>3}分"
                f"  sig:{sig:>3}{raw_note}{calib_note}{label_str}"
                f"  {verdict[:30]:30s}"
                f"  {item['headline'][:60]}",
                file=sys.stderr,
            )
        print(file=sys.stderr)

    scored_tuples = [(item, item.get("ai_score", 0)) for item in all_scored]
    diverse = _select_top_n(
        scored_tuples, max_total=top_n, max_bilibili=max_bilibili,
        max_per_source=cfg["max_per_source"],
    )
    return diverse[:top_n]


# ═════════════════════════════════════════════════════════════
# Content similarity — cross-language duplicate detection
# ═════════════════════════════════════════════════════════════

# Words too generic to be a signal for story-level similarity.
# Filtered out so that two stories sharing only a topic (e.g. both
# mention "Tencent") don't trigger a false duplicate match.
_DEDUP_NOISE: set[str] = {
    # Chinese noise
    "报告", "报道", "文章", "分析", "认为", "表示", "指出", "显示",
    "该新闻", "反映了", "目前", "已经", "可以", "进行", "这个",
    "通过", "以及", "包括", "对于", "根据", "作为", "不仅",
    "游戏", "新闻", "近日", "本周", "资讯", "了解", "据悉",
    # English noise
    "the", "and", "for", "this", "that", "with", "from", "game",
    "news", "report", "reports", "reported", "says", "said",
}


def _is_same_story(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Check whether two scored candidates describe the same underlying event.

    Designed to catch cross-language duplicates — for example when a
    GameLook article translates a Bloomberg scoop and pocketgamer.biz
    reports the same Bloomberg story in English.  Because the headlines
    share almost no tokens (one is Chinese, one is English), the Phase A
    cross-source token dedup misses them.

    We use the AI-written summaries, which are both in Chinese, to
    compute Jaccard similarity on meaningful (non-noise) tokens.
    """

    def _tokens(text: str) -> set[str]:
        """Extract meaningful tokens from AI summary for similarity comparison.

        Handles both English (word-level) and Chinese (character bigrams).
        Chinese text has no whitespace word boundaries, so character bigrams
        are the standard approach for measuring content overlap without a
        segmenter like jieba.
        """
        if not text:
            return set()
        text_lower = text.lower()
        tokens: set[str] = set()
        # English words: sequences of alphabetic characters (2+ chars)
        tokens.update(re.findall(r'[a-z]{2,}', text_lower))
        # Chinese: character bigrams (sliding window of 2 adjacent CJK chars)
        # This catches phrases like "腾讯" "退出" "日本" "谈判" etc.
        cjk = re.findall(r'[一-鿿]', text_lower)
        for i in range(len(cjk) - 1):
            tokens.add(cjk[i] + cjk[i + 1])
        return tokens - _DEDUP_NOISE

    # ── Primary signal: AI summary overlap ──
    summary_a = (a.get("ai_summary") or "").strip()
    summary_b = (b.get("ai_summary") or "").strip()
    if summary_a and summary_b:
        toks_a = _tokens(summary_a)
        toks_b = _tokens(summary_b)
        if toks_a and toks_b:
            overlap = len(toks_a & toks_b)
            union = len(toks_a | toks_b)
            if union > 0:
                jaccard = overlap / union
                # Two AI summaries of the same event in Chinese typically
                # score 0.18+ Jaccard on character bigrams.  Different
                # events are well below 0.05.  Threshold of 0.15 gives a
                # safe margin.
                if jaccard >= 0.15:
                    return True
            # Named-entity fallback: 2+ shared entities (companies,
            # proper nouns) signal the same event even when paraphrasing
            # dilutes bigram overlap.
            ents_a = _named_entities(summary_a)
            ents_b = _named_entities(summary_b)
            if len(ents_a & ents_b) >= 2:
                return True

    # ── Fallback: headline named-entity overlap ──
    # Used when summaries are not yet available for both items.
    # Extract English proper nouns (capitalised words ≥ 3 chars) plus
    # known Chinese game-company names.
    headline_a = (a.get("headline") or "").strip()
    headline_b = (b.get("headline") or "").strip()
    if headline_a and headline_b:
        ents_a = _named_entities(headline_a)
        ents_b = _named_entities(headline_b)
        if len(ents_a & ents_b) >= 2:
            return True

    return False


def _named_entities(text: str) -> set[str]:
    """Extract a small set of story-identifying named entities from text."""
    entities: set[str] = set()
    # English proper nouns (e.g. Tencent, Marvelous, Bloomberg).
    # Exclude common title-case words that aren't named entities.
    _EN_STOP: set[str] = {
        "The", "And", "For", "From", "With", "Japanese",
        # Generic title-case words (not story-identifying entities)
        "Game", "Games", "New", "Best", "Top", "Mobile", "Latest",
        "Report", "Studio", "Industry", "Market", "Global", "World",
        "First", "Major", "Next", "Big", "Future", "More", "Most",
        "Year", "Week", "Day", "News", "Weekly", "Daily",
    }
    entities.update(
        w for w in re.findall(r'[A-Z][a-z]{2,}(?:[A-Z][a-z]+)*', text)
        if w not in _EN_STOP
    )
    # Known Chinese game-company names (case-insensitive match)
    cn_companies = [
        "腾讯", "网易", "米哈游", "字节跳动", "莉莉丝", "叠纸", "鹰角",
        "完美世界", "三七互娱", "索尼", "任天堂", "微软",
    ]
    for c in cn_companies:
        if c in text:
            entities.add(c)
    return entities


# ═════════════════════════════════════════════════════════════
# Diversity-aware top-N selection (after AI scoring)
# ═════════════════════════════════════════════════════════════

def _select_top_n(
    scored_items: list[tuple[dict[str, Any], float]],
    max_total: int = 7,
    max_bilibili: int = 2,
    max_per_source: int = 3,
) -> list[dict[str, Any]]:
    """Greedy top-N by AI score, with source diversity caps.

    Constraints:
      - Max N items total
      - Max max_bilibili items from B站
      - Max max_per_source items from any single source
      - Max 2 items about the same game (extracted from 《》 brackets)
      - At least 1 overseas (pocketgamer.biz) item when available
      - No duplicate stories (cross-language dedup via AI summary similarity)

    Returns items in score-descending order subject to caps.
    """
    from src.pipeline.token_utils import _RE_GAME_NAMES
    from src.pipeline.source_constants import normalize_source

    scored_items.sort(key=lambda x: x[1], reverse=True)

    def _extract_game(headline: str) -> str:
        m = _RE_GAME_NAMES.search(headline)
        return m.group(1).strip() if m else headline[:8]

    selected: list[dict[str, Any]] = []
    seen_games: dict[str, int] = {}
    source_counts: dict[str, int] = {}

    def _overseas_count() -> int:
        return source_counts.get(NewsSource.POCKET_GAMER, 0)

    # ── Pass 1: greedy score-based selection ──
    # Reserve a slot for overseas if none has been picked yet and
    # overseas items exist in the pool (prevents Pass 1 from filling
    # all slots, which would defeat Pass 2's overseas guarantee).
    _has_overseas = any(
        is_overseas((item.get("source", "") or "").lower())
        for item, _ in scored_items
    ) if _overseas_count() == 0 else False

    for item, _score in scored_items:
        # Dynamic cap: reserve last slot for overseas when needed,
        # but release it once an overseas item has been picked.
        _need_reserve = _has_overseas and _overseas_count() == 0
        if len(selected) >= (max_total - 1 if _need_reserve else max_total):
            break

        src = normalize_source(item.get("source", "")) or ""

        if source_counts.get(src, 0) >= max_per_source:
            continue

        if is_bilibili(src) and source_counts.get(NewsSource.BILIBILI, 0) >= max_bilibili:
            continue

        game = _extract_game(item.get("headline", ""))
        if seen_games.get(game, 0) >= 2:
            continue

        # Cross-language dedup: skip if same story as an already-selected item
        if any(_is_same_story(item, s) for s in selected):
            continue

        selected.append(item)
        seen_games[game] = seen_games.get(game, 0) + 1
        source_counts[src] = source_counts.get(src, 0) + 1

    # ── Pass 2: ensure at least 1 overseas source ──
    if _overseas_count() < 1:
        for item, _score in scored_items:
            if _overseas_count() >= 1:
                break
            if item in selected:
                continue

            item_src = (item.get("source", "") or "").lower()
            if not is_overseas(item_src):
                continue

            if source_counts.get(NewsSource.POCKET_GAMER, 0) >= max_per_source * 2:
                continue

            game = _extract_game(item.get("headline", ""))
            if seen_games.get(game, 0) >= 2:
                continue

            # Cross-language dedup (same check as Pass 1)
            if any(_is_same_story(item, s) for s in selected):
                continue

            selected.append(item)
            seen_games[game] = seen_games.get(game, 0) + 1
            source_counts[NewsSource.POCKET_GAMER] = source_counts.get(NewsSource.POCKET_GAMER, 0) + 1

    # ── Pass 3: fill any remaining slots (reserved for overseas
    # but no qualifying overseas item was found in Pass 2) ──
    if len(selected) < max_total:
        for item, _score in scored_items:
            if len(selected) >= max_total:
                break
            if item in selected:
                continue

            src = normalize_source(item.get("source", "")) or ""

            if source_counts.get(src, 0) >= max_per_source:
                continue

            if is_bilibili(src) and source_counts.get(NewsSource.BILIBILI, 0) >= max_bilibili:
                continue

            game = _extract_game(item.get("headline", ""))
            if seen_games.get(game, 0) >= 2:
                continue

            if any(_is_same_story(item, s) for s in selected):
                continue

            selected.append(item)
            seen_games[game] = seen_games.get(game, 0) + 1
            source_counts[src] = source_counts.get(src, 0) + 1

    return selected
