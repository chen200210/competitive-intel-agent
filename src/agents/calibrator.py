"""
Calibrator Agent — feedback-driven scoring parameter tuning.

Consumes user_feedback data (👍/👎 on daily report news), uses a pure
rule engine to discover preference patterns (zero token), and outputs
versioned calibration parameters that the Summarizer reads to adjust
its scoring.

Runs weekly (or on demand when feedback accumulates ≥30 items).
Design inspired by RecGPT's LLM-as-a-Judge evaluation chain — the
LLM has been replaced by a statistically-equivalent rule engine (RED-3).

Usage:
    python -m src.agents.calibrator --days 14
    python -m src.agents.calibrator --start 2026-06-11 --end 2026-06-25
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import BaseModel, model_validator

from src.pipeline.token_utils import ascii_word_match


# ═════════════════════════════════════════════════════════════
# Allowed topic keys (must match prompts/calibrator.yaml Matched Interest Pool)
# ═════════════════════════════════════════════════════════════

ALLOWED_TOPIC_KEYS: frozenset[str] = frozenset({
    "独立游戏", "AI游戏", "大厂动态", "人事变动", "海外市场",
    "买量数据", "小游戏", "Steam移植", "新游首曝", "版号",
    "投融资", "IP授权", "电竞", "二次元",
})

DEFAULT_DIM_WEIGHTS: dict[str, int] = {"track": 40, "density": 40, "insight": 20}

# ═════════════════════════════════════════════════════════════
# Topic → keyword map (rule engine)
# ═════════════════════════════════════════════════════════════
# Each ALLOWED_TOPIC_KEY is mapped to a list of Chinese/English
# keyword patterns.  The rule engine scans feedback headlines
# for these keywords to attribute user sentiment to topics.
#
# When adding a new topic to ALLOWED_TOPIC_KEYS, add its keyword
# list here too — the rule engine looks up this map, not the YAML.

TOPIC_KEYWORD_MAP: dict[str, list[str]] = {
    "独立游戏": ["独立游戏", "indie", "独立开发", "个人开发者", "独游"],
    "AI游戏": ["AI", "人工智能", "AIGC", "大模型", "LLM", "GPT", "AI游戏"],
    "大厂动态": ["腾讯", "网易", "米哈游", "字节", "莉莉丝", "叠纸", "鹰角",
                  "三七", "完美世界", "巨人"],
    "人事变动": ["离职", "入职", "任命", "CEO", "高管", "人事", "裁员", "跳槽"],
    "海外市场": ["海外", "出海", "全球", "欧美", "日本", "韩国", "东南亚", "中东",
                 "北美", "欧洲"],
    "买量数据": ["买量", "投放", "广告", "CPI", "ROI", "LTV", "获客", "买量成本"],
    "小游戏": ["小游戏", "小程序", "微信小游戏", "H5游戏", "迷你游戏", "休闲游戏"],
    "Steam移植": ["Steam", "steam", "移植", "端游", "PC版", "PC端"],
    "新游首曝": ["首曝", "首测", "首爆", "定档", "首曝", "首测"],
    "版号": ["版号", "审批", "过审"],
    "投融资": ["融资", "投资", "收购", "并购", "IPO", "估值", "融资额"],
    "IP授权": ["IP", "联动", "授权", "改编", "合作IP"],
    "电竞": ["电竞", "赛事", "联赛", "冠军", "eSports", "锦标赛", "总决赛"],
    "二次元": ["二次元", "动漫", "anime", "日系", "原神", "崩坏", "少女前线"],
}


# ═════════════════════════════════════════════════════════════
# Output schema
# ═════════════════════════════════════════════════════════════

class CalibratorFinding(BaseModel):
    """A single pattern discovered from feedback analysis."""
    pattern: str = ""
    evidence_count: int = 0
    action: str = ""
    confidence: str = "medium"   # high | medium | low


class CalibratorOutput(BaseModel):
    """Validated output from the Calibrator rule engine.

    Includes a model_validator that enforces the Matched Interest Pool
    contracts declared in prompts/calibrator.yaml:
      - topic_boosts values must be in [-20, 20]
      - dim_weights keys must be the three recognized dimensions
      - dim_weights must sum to 100
    Unknown topic keys are dropped with a warning; out-of-range values
    are clamped.  This prevents one bad output from poisoning the
    calibration history (Angle G finding 3).
    """
    topic_boosts: dict[str, int] = {}
    dim_weights: dict[str, int] = {}
    findings: list[CalibratorFinding] = []
    summary: str = ""

    @model_validator(mode="after")
    def _validate_params(self) -> "CalibratorOutput":
        # ── Filter unknown topic keys ──
        unknown = [k for k in self.topic_boosts if k not in ALLOWED_TOPIC_KEYS]
        for k in unknown:
            print(
                f"  [WARN] Calibrator output unknown topic key '{k}' — dropped",
                file=sys.stderr,
            )
            del self.topic_boosts[k]

        # ── Clamp topic_boost values to [-20, 20] ──
        for k, v in list(self.topic_boosts.items()):
            if not isinstance(v, (int, float)):
                print(
                    f"  [WARN] Calibrator output non-numeric boost for '{k}': {v} — dropped",
                    file=sys.stderr,
                )
                del self.topic_boosts[k]
            elif v < -20:
                self.topic_boosts[k] = -20
            elif v > 20:
                self.topic_boosts[k] = 20

        # ── Validate dim_weights ──
        valid_dims = {"track", "density", "insight"}
        extra_dims = [k for k in self.dim_weights if k not in valid_dims]
        for k in extra_dims:
            print(
                f"  [WARN] Calibrator output unknown dim_weight key '{k}' — dropped",
                file=sys.stderr,
            )
            del self.dim_weights[k]

        total = sum(self.dim_weights.values())
        if total != 100:
            print(
                f"  [WARN] Calibrator output dim_weights sum={total} (expected 100) — "
                f"resetting to defaults",
                file=sys.stderr,
            )
            self.dim_weights = dict(DEFAULT_DIM_WEIGHTS)

        return self


# ═════════════════════════════════════════════════════════════
# Feedback fidelity check — zero-token signal functions
# ═════════════════════════════════════════════════════════════
#
# Each feedback row is evaluated across 4 independent signals to estimate
# P(this feedback reflects genuine user preference | observable evidence).
# Low-fidelity rows (< _FIDELITY_DROP) are discarded before Calibrator sees
# them, preventing noise-driven parameter drift (RSIR self-consuming collapse).
#
# Design doc: docs/FEEDBACK_FIDELITY_DESIGN.md

_FIDELITY_DROP = 0.30        # Below this → discard (likely noise)
_FIDELITY_MEDIUM = 0.60      # Below this → keep but mark medium-confidence

# ── Sentiment constants — single source of truth ──
# Shared by _aggregate_feedback (producer) and _signal_ai_feedback_consistency
# (consumer).  Changing these strings in one place updates both.
_SENTIMENT_UP = "👍偏好"
_SENTIMENT_DOWN = "👎排斥"
_SENTIMENT_NEUTRAL = "⚖️中性"

# ── AI label sets for fidelity signals ──
# These are INTENTIONAL SUBSETS of _VALID_POS_LABELS / _VALID_NEG_LABELS
# defined in scorer.py.  We only include labels that indicate a *clear* AI
# quality judgment — weaker labels like "playable_reference", "ai_related",
# "digest_rerun", "generic_pr" are excluded because they don't provide a
# strong enough signal for the fidelity triage.  If you add a label to
# scorer.py that represents a clear quality judgment, add it here too.
_AI_POSITIVE_LABELS: frozenset[str] = frozenset({
    "track_direct", "high_info_density", "exclusive_scoop", "overseas_insight",
})
_AI_NEGATIVE_LABELS: frozenset[str] = frozenset({
    "off_track", "no_gameplay", "low_info", "stale",
})


def _signal_ai_feedback_consistency(row: dict[str, Any]) -> float:
    """40% weight — agreement between two independent systems (AI + user).

    Key insight: AI and user are independent judges of the same news item.
    Agreement → strong evidence of genuine quality (or genuine poor quality).
    Disagreement → still valuable (may reveal AI blindspots) but less certain.
    No AI labels → weakest signal (no reference point to cross-check).
    """
    pos = (row.get("pos_label") or "").strip()
    neg = (row.get("neg_label") or "").strip()
    sentiment = row.get("sentiment", "")

    ai_positive = pos in _AI_POSITIVE_LABELS
    ai_negative = neg in _AI_NEGATIVE_LABELS
    user_positive = sentiment == _SENTIMENT_UP
    user_negative = sentiment == _SENTIMENT_DOWN

    # Strong agreement: AI + user independently confirm each other
    if ai_positive and user_positive:
        return 1.0
    if ai_negative and user_negative:
        return 0.85  # slightly weaker: users 👎 less consistently than 👍

    # Disagreement: AI and user contradict.  This is NOT noise — it's the
    # most valuable signal for Calibrator to analyze (potential AI blindspot
    # or user misunderstanding).  But fidelity is medium — we can't be sure
    # which side is right without deeper analysis.
    if (ai_positive and user_negative) or (ai_negative and user_positive):
        return 0.40

    # AI has an opinion but user is neutral
    if (ai_positive or ai_negative) and sentiment == _SENTIMENT_NEUTRAL:
        return 0.25

    # No AI labels → no reference point to triangulate
    return 0.15


def _signal_feedback_concentration(row: dict[str, Any]) -> float:
    """30% weight — participation depth, moderated by consensus clarity.

    A single 👍 (total=1) is an anecdote, not evidence — depth guards that.
    But high participation with a 3-2 split (5 voters, ratio=0.2) is MORE
    informative than a 2-0 sweep (2 voters, ratio=1.0).  The formula
    `depth * (0.5 + 0.5*ratio)` ensures that more voters → higher score,
    with unanimous consensus adding at most a 2× bonus over a 50/50 split.
    """
    up = int(row.get("up", 0))
    down = int(row.get("down", 0))
    total = up + down
    if total == 0:
        return 0.0

    # How unanimous?  0 = evenly split, 1 = all agree
    ratio = abs(up - down) / total

    # Participation depth — maps total→[0,1]
    if total >= 5:
        depth = 1.0
    elif total >= 3:
        depth = 0.75
    elif total >= 2:
        depth = 0.40
    else:
        depth = 0.15   # single-user anecdote

    # Blend: depth dominates, ratio provides a ±50% modulation.
    # 5 voters 3-2 (ratio=0.2, depth=1.0) → 0.60  >  2 voters 2-0 (ratio=1.0, depth=0.40) → 0.40
    # 5 voters 5-0 (ratio=1.0, depth=1.0) → 1.00  >  all others
    return depth * (0.5 + 0.5 * ratio)


def _signal_track_anchor(row: dict[str, Any]) -> float:
    """20% weight — is the feedback target inside our domain of interest?

    Feedback on track-relevant games (塔防/肉鸽/割草) comes from the target
    audience whose preferences we're optimizing for.  Feedback on clearly
    excluded categories is more likely accidental or misaligned.
    """
    return 1.0 if row.get("track_relevant") else 0.0


def _signal_label_coverage(row: dict[str, Any]) -> float:
    """10% weight — can we do label×feedback cross-analysis?

    When AI labels exist, Calibrator can pivot: "users 👍 track_direct news
    but 👎 no_gameplay news".  Without labels, one analysis dimension is lost.
    """
    has_label = bool(
        (row.get("pos_label") or "").strip()
        or (row.get("neg_label") or "").strip()
    )
    return 1.0 if has_label else 0.0


def _compute_fidelity(row: dict[str, Any]) -> float:
    """Estimate P(feedback is genuine preference | observable evidence).

    Pure function — zero token, zero DB access.  Weights are heuristic,
    calibrated to produce the following reference points:

        Off-track + no label + single anecdote   → ≤0.11  (dropped)
        Off-track + weak label + single 👍       → ~0.20  (dropped)
        Track + weak label + single 👍           → ~0.41  (medium)
        Track + track_direct + 3👍 consensus     → ~0.93  (high)
        Track + track_direct + 5👍 consensus     →  1.00  (very high)
    """
    return round(
        0.40 * _signal_ai_feedback_consistency(row)
        + 0.30 * _signal_feedback_concentration(row)
        + 0.20 * _signal_track_anchor(row)
        + 0.10 * _signal_label_coverage(row),
        3,
    )


# ═════════════════════════════════════════════════════════════
# Feedback aggregation (zero token, SQL only)
# ═════════════════════════════════════════════════════════════

def _aggregate_feedback(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Query user_feedback joined with market_news for analysis.

    Returns one row per unique news_url, with up/down counts, headline,
    source, and (when available) the AI labels from the report.

    Rows with fidelity below _FIDELITY_DROP are discarded before returning.
    Each kept row includes a ``_fidelity`` key for downstream formatting.

    Uses user_feedback.news_url as the join key to market_news.url.
    """
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        rows = db._connect().execute("""
            SELECT
                uf.news_url as url,
                SUM(CASE WHEN uf.feedback_type = 'thumbs_up' THEN 1 ELSE 0 END) as up,
                SUM(CASE WHEN uf.feedback_type = 'thumbs_down' THEN 1 ELSE 0 END) as down,
                mn.headline,
                mn.source,
                mn.track_relevant,
                mn.pos_label,
                mn.neg_label
            FROM user_feedback uf
            LEFT JOIN (
                SELECT url, headline, source, track_relevant, pos_label, neg_label
                FROM market_news
                GROUP BY url
            ) mn ON mn.url = uf.news_url
            WHERE uf.date >= ? AND uf.date <= ?
              AND uf.news_url != ''
            GROUP BY uf.news_url
            ORDER BY (up + down) DESC, up DESC
        """, (start_date, end_date)).fetchall()

        result: list[dict[str, Any]] = []
        dropped = 0
        for r in rows:
            net = r["up"] - r["down"]
            if net > 0:
                sentiment = _SENTIMENT_UP
            elif net < 0:
                sentiment = _SENTIMENT_DOWN
            else:
                sentiment = _SENTIMENT_NEUTRAL
            row_dict = {
                "headline": (r["headline"] or r["url"] or "?")[:60],
                "source": (r["source"] or "?")[:12],
                "up": r["up"],
                "down": r["down"],
                "sentiment": sentiment,
                "track_relevant": bool(r["track_relevant"]),
                "pos_label": (r["pos_label"] or ""),
                "neg_label": (r["neg_label"] or ""),
            }

            # ── Fidelity gate: discard likely-noise feedback ──
            fid = _compute_fidelity(row_dict)
            if fid < _FIDELITY_DROP:
                dropped += 1
                continue
            row_dict["_fidelity"] = fid
            result.append(row_dict)

        if dropped:
            print(
                f"  Fidelity filter: {dropped} low-fidelity feedback row(s) dropped "
                f"({len(result)} kept)",
                file=sys.stderr,
            )
        return result
    except Exception as e:
        print(f"  [WARN] Feedback aggregation failed: {e}", file=sys.stderr)
        return []


# ═════════════════════════════════════════════════════════════
# Current params loader (shared)
# ═════════════════════════════════════════════════════════════

def _load_params_base() -> dict[str, Any] | None:
    """Return the latest calibration params row, or None if DB is empty/unavailable.

    Pure reader — no side effects.  Callers layer on their own semantics:
      - _load_current_params: adds 'summary' for prompt formatting
      - load_calibration_for_scorer: adds mark_calibration_applied + returns scorer-ready shape
    """
    try:
        from src.storage.sqlite import get_db
        db = get_db()
        return db.get_latest_calibration_params()
    except Exception as e:
        print(f"  [WARN] _load_params_base failed: {e}", file=sys.stderr)
        return None


def _load_current_params() -> dict[str, Any]:
    """Load the latest calibration params, or return bootstrapping defaults."""
    latest = _load_params_base()
    if latest:
        return {
            "topic_boosts": latest["topic_boosts"],
            "dim_weights": latest["dim_weights"],
            "version": latest["version"],
            "summary": latest["summary"],
        }
    return {
        "topic_boosts": {},
        "dim_weights": dict(DEFAULT_DIM_WEIGHTS),
        "version": 0,
        "summary": "初始默认参数（尚无校准数据）",
    }


# ═════════════════════════════════════════════════════════════
# Rule engine — zero-token feedback analysis (RED-3)
# ═════════════════════════════════════════════════════════════
#
# Replaces the LLM call that previously analyzed the feedback table.
# The decision rules in prompts/calibrator.yaml have been translated
# to pure Python — every threshold and constraint is preserved.
#
# The rule engine produces the same CalibratorOutput schema as the
# LLM did, so downstream persistence and scorer integration are
# completely unchanged.

_MIN_EVIDENCE_UP = 5       # ≥5 👍 on same topic → positive boost
_MIN_EVIDENCE_DOWN = 3     # ≥3 👎 on same topic → negative boost
_MAX_DIM_DELTA = 15        # Single dim_weight adjustment cap
_DIM_MIN = 10              # Floor for any single dimension
_DIM_MAX = 70              # Ceiling for any single dimension


def _match_topic(headline: str, topic: str) -> bool:
    """Check whether *headline* contains any keyword for *topic*.

    Delegates to token_utils.ascii_word_match() for consistent
    word-boundary handling across the codebase.
    """
    keywords = TOPIC_KEYWORD_MAP.get(topic, [])
    if not keywords:
        return False
    for kw in keywords:
        if ascii_word_match(kw, headline):
            return True
    return False


def _analyze_feedback_rules(
    feedback: list[dict[str, Any]],
    current_params: dict[str, Any],
    total_feedback: int,
    unique_news: int,
    verbose: bool = False,
) -> "CalibratorOutput":
    """Analyze aggregated feedback with a pure rule engine (zero LLM tokens).

    Args:
        feedback:       Aggregated rows from ``_aggregate_feedback()``.
        current_params: Latest calibration params (or bootstrapped defaults).
        total_feedback: Sum of up+down across all rows.
        unique_news:    Number of unique news URLs.
        verbose:        Log decisions to stderr.

    Returns:
        ``CalibratorOutput`` with topic_boosts, dim_weights, findings, summary.
    """
    prev_topic_boosts: dict[str, int] = current_params.get("topic_boosts", {})
    prev_dim_weights: dict[str, int] = current_params.get(
        "dim_weights", dict(DEFAULT_DIM_WEIGHTS)
    )

    # ── Phase A: topic_boosts from headline keyword matching ──
    topic_boosts: dict[str, int] = {}
    topic_evidence: dict[str, dict[str, int]] = {}

    for topic in ALLOWED_TOPIC_KEYS:
        matched = [f for f in feedback if _match_topic(f.get("headline", ""), topic)]
        if not matched:
            continue
        up = sum(f["up"] for f in matched)
        down = sum(f["down"] for f in matched)
        net = up - down

        topic_evidence[topic] = {"up": up, "down": down, "count": len(matched)}

        boost = 0
        if down >= _MIN_EVIDENCE_DOWN and net < 0:
            boost = max(-20, -10 - (down - _MIN_EVIDENCE_DOWN) * 2)
        elif up >= _MIN_EVIDENCE_UP and net > 0:
            boost = min(20, 5 + (up - _MIN_EVIDENCE_UP) * 2)

        if boost != 0:
            topic_boosts[topic] = boost

    # ── Merge with previous topic_boosts (symmetric decay for absent topics) ──
    # Both positive and negative boosts decay at 50% per run when the topic
    # has no new evidence.  Once the absolute value reaches 0 the entry is
    # dropped — no lingering near-zero noise.
    for topic, prev_val in prev_topic_boosts.items():
        if topic not in topic_boosts:
            if prev_val > 0:
                decayed = prev_val // 2
            elif prev_val < 0:
                decayed = -((-prev_val) // 2)   # round toward zero
            else:
                decayed = 0
            if decayed != 0:
                topic_boosts[topic] = decayed

    # ── Phase B: dim_weights from label × sentiment analysis ──
    track_rows = [f for f in feedback if f.get("track_relevant")]
    track_up = sum(f["up"] for f in track_rows)
    track_down = sum(f["down"] for f in track_rows)
    track_net = track_up - track_down

    density_rows = [
        f for f in feedback
        if "high_info_density" in (f.get("pos_label") or "")
        or "low_info" in (f.get("neg_label") or "")
    ]
    density_up = sum(f["up"] for f in density_rows)
    density_down = sum(f["down"] for f in density_rows)
    density_net = density_up - density_down

    insight_rows = [
        f for f in feedback
        if "overseas_insight" in (f.get("pos_label") or "")
        or "exclusive_scoop" in (f.get("pos_label") or "")
    ]
    insight_up = sum(f["up"] for f in insight_rows)
    insight_down = sum(f["down"] for f in insight_rows)
    insight_net = insight_up - insight_down

    # Evidence counts per dimension — number of feedback rows that support each signal.
    # Distinct from abs(delta) which measures weight-adjustment magnitude (capped at ±5).
    dim_row_counts: dict[str, int] = {
        "track": len(track_rows),
        "density": len(density_rows),
        "insight": len(insight_rows),
    }

    dim_signals = [
        ("track", track_net, prev_dim_weights.get("track", 40)),
        ("density", density_net, prev_dim_weights.get("density", 40)),
        ("insight", insight_net, prev_dim_weights.get("insight", 20)),
    ]

    new_dim_weights: dict[str, int] = {}
    dim_deltas: dict[str, int] = {}
    for dim, net, prev_val in dim_signals:
        if net >= 3:
            delta = min(_MAX_DIM_DELTA, 5 + (net - 3))
        elif net <= -3:
            delta = max(-_MAX_DIM_DELTA, -5 - (abs(net) - 3))
        else:
            delta = 0

        new_val = prev_val + delta
        new_val = max(_DIM_MIN, min(_DIM_MAX, new_val))
        new_dim_weights[dim] = new_val
        dim_deltas[dim] = delta

    # Normalize to sum=100
    total_w = sum(new_dim_weights.values())
    if total_w != 100:
        scaled = {k: round(v * 100 / total_w) for k, v in new_dim_weights.items()}
        diff = 100 - sum(scaled.values())
        largest_dim = max(scaled, key=scaled.get)  # type: ignore[arg-type]
        scaled[largest_dim] += diff
        new_dim_weights = scaled

    # ── Phase C: findings ──
    findings: list[CalibratorFinding] = []

    for topic in sorted(topic_boosts, key=lambda t: abs(topic_boosts[t]), reverse=True):
        boost = topic_boosts[topic]
        ev = topic_evidence.get(topic, {"up": 0, "down": 0, "count": 0})
        evidence_count = ev["count"]
        # Skip decay-only topics — no current-run evidence to report
        if evidence_count == 0:
            continue
        if boost > 0:
            pattern = f"{topic}话题报道持续获用户好评（{ev['up']}👍 / {ev['down']}👎）"
            action = f"topic_boosts.{topic} +{boost}"
            confidence = "high" if ev["up"] >= _MIN_EVIDENCE_UP + 2 else "medium"
        else:
            pattern = f"{topic}话题报道持续受用户排斥（{ev['up']}👍 / {ev['down']}👎）"
            action = f"topic_boosts.{topic} {boost}"
            confidence = "high" if ev["down"] >= _MIN_EVIDENCE_DOWN + 2 else "medium"
        findings.append(CalibratorFinding(
            pattern=pattern,
            evidence_count=evidence_count,
            action=action,
            confidence=confidence,
        ))

    for dim in ("track", "density", "insight"):
        delta = dim_deltas.get(dim, 0)
        if delta != 0:
            prev_val = prev_dim_weights.get(dim, DEFAULT_DIM_WEIGHTS[dim])
            new_val = new_dim_weights[dim]
            direction = "正向" if delta > 0 else "负向"
            findings.append(CalibratorFinding(
                pattern=f"{dim}维度用户反馈净{direction}（净{'+' if delta > 0 else ''}{delta}），调整权重",
                evidence_count=dim_row_counts.get(dim, 0),
                action=f"dim_weights.{dim} {prev_val}→{new_val}",
                confidence="medium",
            ))

    # ── Phase D: summary ──
    topic_lines: list[str] = []
    for topic in sorted(topic_boosts, key=lambda t: abs(topic_boosts[t]), reverse=True):
        boost = topic_boosts[topic]
        topic_lines.append(f"  - {topic}: {'+' if boost > 0 else ''}{boost}")

    dim_lines: list[str] = []
    for dim in ("track", "density", "insight"):
        pv = prev_dim_weights.get(dim, DEFAULT_DIM_WEIGHTS[dim])
        nv = new_dim_weights[dim]
        if pv != nv:
            dim_lines.append(f"  - {dim}: {pv}→{nv}")
        else:
            dim_lines.append(f"  - {dim}: {nv} (未调整)")

    summary = (
        f"规则引擎校准（{total_feedback}条反馈，{unique_news}条新闻）。\n"
        f"topic_boosts 调整：\n"
        + ("\n".join(topic_lines) if topic_lines else "  （无显著信号）")
        + f"\ndim_weights 调整：\n"
        + "\n".join(dim_lines)
        + f"\n调整规则：👍≥{_MIN_EVIDENCE_UP}次正向加成，👎≥{_MIN_EVIDENCE_DOWN}次负向惩罚，"
        f"维度权重单次调整≤±{_MAX_DIM_DELTA}%。"
    )

    if verbose:
        if topic_boosts:
            print(f"  [rules] topic_boosts: {topic_boosts}", file=sys.stderr)
        else:
            print("  [rules] topic_boosts: (no significant signal)", file=sys.stderr)
        print(f"  [rules] dim_weights: {new_dim_weights}", file=sys.stderr)

    return CalibratorOutput(
        topic_boosts=topic_boosts,
        dim_weights=new_dim_weights,
        findings=findings,
        summary=summary,
    )


# ═════════════════════════════════════════════════════════════
# Main entry point
# ═════════════════════════════════════════════════════════════

_MIN_FEEDBACK_THRESHOLD = 30


def run_calibrator(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 14,
    force: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run the Calibrator: aggregate feedback → rule analysis → persist params.

    Args:
        start_date:  Start of feedback window (YYYY-MM-DD).  Computed from
                     ``end_date - days`` if not provided.
        end_date:    End of feedback window (YYYY-MM-DD).  Defaults to today.
        days:        Number of days to look back when start_date is unset.
        force:       Run even when feedback count < _MIN_FEEDBACK_THRESHOLD.
        verbose:     Print progress to stderr.

    Returns:
        Dict with keys: version, topic_boosts, dim_weights, findings,
        summary, total_feedback_count, skipped (bool).
    """
    from datetime import datetime, timedelta

    # ── Resolve date range ──
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if start_date is None:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_date = (end_dt - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    if verbose:
        print(f"Calibrator: analyzing feedback from {start_date} to {end_date}",
              file=sys.stderr)

    # ── Step 1: Aggregate feedback (zero token) ──
    feedback = _aggregate_feedback(start_date, end_date)
    total_feedback = sum(f["up"] + f["down"] for f in feedback)
    unique_news = len(feedback)

    if verbose:
        print(f"  Aggregated {total_feedback} feedback items across {unique_news} news articles",
              file=sys.stderr)
        medium_count = sum(
            1 for f in feedback
            if f.get("_fidelity", 1.0) < _FIDELITY_MEDIUM
        )
        if medium_count:
            print(
                f"    ({medium_count} item(s) marked medium-confidence, "
                f"fidelity < {_FIDELITY_MEDIUM})",
                file=sys.stderr,
            )

    # ── Gate: insufficient data ──
    if total_feedback < _MIN_FEEDBACK_THRESHOLD and not force:
        msg = (
            f"仅 {total_feedback} 条高信度反馈（经保真度过滤），"
            f"不足 {_MIN_FEEDBACK_THRESHOLD} 条阈值。"
            f"跳过校准。使用 --force 强制运行。"
        )
        if verbose:
            print(f"  {msg}", file=sys.stderr)
        return {
            "skipped": True,
            "reason": msg,
            "total_feedback_count": total_feedback,
            "unique_news": unique_news,
        }

    # ── Step 2: Load current params ──
    current_params = _load_current_params()
    if verbose:
        print(f"  Current params: v{current_params['version']}", file=sys.stderr)

    # ── Step 3: Rule engine analysis (zero LLM tokens) ──
    try:
        calib_output = _analyze_feedback_rules(
            feedback=feedback,
            current_params=current_params,
            total_feedback=total_feedback,
            unique_news=unique_news,
            verbose=verbose,
        )
        result = calib_output.model_dump()
    except Exception as e:
        print(f"  [ERROR] Calibrator rule engine failed: {e}", file=sys.stderr)
        return {
            "skipped": True,
            "reason": f"规则引擎异常: {e}",
            "total_feedback_count": total_feedback,
            "unique_news": unique_news,
        }

    # ── Step 4: Persist ──
    topic_boosts = result.get("topic_boosts") or current_params.get("topic_boosts", {})
    dim_weights = result.get("dim_weights") or current_params.get("dim_weights", {})
    findings = result.get("findings") or []
    summary = result.get("summary", "")

    try:
        from src.storage.sqlite import get_db
        db = get_db()
        version = db.insert_calibration_params(
            topic_boosts=topic_boosts,
            dim_weights=dim_weights,
            findings=findings,
            summary=summary,
            feedback_start_date=start_date,
            feedback_end_date=end_date,
            total_feedback_count=total_feedback,
        )
    except Exception as e:
        print(f"  [ERROR] Failed to persist calibration params: {e}", file=sys.stderr)
        return {
            "skipped": True,
            "reason": f"DB写入失败: {e}",
            "total_feedback_count": total_feedback,
        }

    if verbose:
        print(f"  Saved calibration v{version}: {summary[:100]}", file=sys.stderr)

    return {
        "skipped": False,
        "version": version,
        "topic_boosts": topic_boosts,
        "dim_weights": dim_weights,
        "findings": findings,
        "summary": summary,
        "total_feedback_count": total_feedback,
        "unique_news": unique_news,
        "feedback_start_date": start_date,
        "feedback_end_date": end_date,
    }


# ═════════════════════════════════════════════════════════════
# Scorer integration — apply calibration to AI scores
# ═════════════════════════════════════════════════════════════

def load_calibration_for_scorer() -> dict[str, Any]:
    """Load calibration params for use in scorer.py.

    Returns a lightweight dict with only the fields scorer needs.
    Also marks the latest version as applied so we can track adoption.

    Returns empty defaults when no calibration data exists yet.
    """
    defaults: dict[str, Any] = {
        "topic_boosts": {},
        "dim_weights": dict(DEFAULT_DIM_WEIGHTS),
        "version": 0,
    }
    latest = _load_params_base()
    if latest is None:
        return defaults
    # Mark as applied if not already (only on the scorer read path —
    # the calibrator's own _load_current_params path does not mark applied)
    try:
        if not latest.get("applied"):
            from src.storage.sqlite import get_db
            get_db().mark_calibration_applied(latest["version"])
    except Exception as e:
        print(f"  [WARN] mark_calibration_applied failed: {e}", file=sys.stderr)
    return {
        "topic_boosts": latest["topic_boosts"],
        "dim_weights": latest["dim_weights"],
        "version": latest["version"],
    }


def apply_topic_boosts(headline: str, ai_score: int, topic_boosts: dict[str, int]) -> int:
    """Apply per-topic score modifiers from calibration.

    Pure function — no side effects, no DB access.
    Each matched topic adds its boost value to the AI score.
    Final score is clamped to [0, 100].
    """
    if not topic_boosts:
        return ai_score

    adjusted = ai_score
    for topic, boost in topic_boosts.items():
        if _match_topic(headline, topic):
            adjusted += boost

    return max(0, min(100, adjusted))


# ═════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Calibrator Agent — feedback-driven scoring parameter tuning"
    )
    parser.add_argument("--days", type=int, default=14,
                        help="Days of feedback to analyze (default 14)")
    parser.add_argument("--start", type=str, default=None,
                        help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None,
                        help="End date (YYYY-MM-DD, default today)")
    parser.add_argument("--force", action="store_true",
                        help="Run even with insufficient feedback")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    result = run_calibrator(
        start_date=args.start,
        end_date=args.end,
        days=args.days,
        force=args.force,
        verbose=args.verbose,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
