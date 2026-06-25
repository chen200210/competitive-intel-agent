"""
Calibrator Agent — feedback-driven scoring parameter tuning.

Consumes user_feedback data (👍/👎 on daily report news), uses LLM to
discover preference patterns, and outputs versioned calibration parameters
that the Summarizer reads to adjust its scoring.

Runs weekly (or on demand when feedback accumulates ≥30 items).
Design inspired by RecGPT's LLM-as-a-Judge evaluation chain.

Usage:
    python -m src.agents.calibrator --days 14
    python -m src.agents.calibrator --start 2026-06-11 --end 2026-06-25
"""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import BaseModel, model_validator


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
# Output schema
# ═════════════════════════════════════════════════════════════

class CalibratorFinding(BaseModel):
    """A single pattern discovered from feedback analysis."""
    pattern: str = ""
    evidence_count: int = 0
    action: str = ""
    confidence: str = "medium"   # high | medium | low


class CalibratorOutput(BaseModel):
    """Validated output from the Calibrator LLM call.

    Includes a model_validator that enforces the Matched Interest Pool
    contracts declared in prompts/calibrator.yaml:
      - topic_boosts values must be in [-20, 20]
      - dim_weights keys must be the three recognized dimensions
      - dim_weights must sum to 100
    Unknown topic keys are dropped with a warning; out-of-range values
    are clamped.  This prevents one bad LLM output from poisoning the
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
                f"  [WARN] Calibrator LLM output unknown topic key '{k}' — dropped",
                file=sys.stderr,
            )
            del self.topic_boosts[k]

        # ── Clamp topic_boost values to [-20, 20] ──
        for k, v in list(self.topic_boosts.items()):
            if not isinstance(v, (int, float)):
                print(
                    f"  [WARN] Calibrator LLM output non-numeric boost for '{k}': {v} — dropped",
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
                f"  [WARN] Calibrator LLM output unknown dim_weight key '{k}' — dropped",
                file=sys.stderr,
            )
            del self.dim_weights[k]

        total = sum(self.dim_weights.values())
        if total != 100:
            print(
                f"  [WARN] Calibrator LLM output dim_weights sum={total} (expected 100) — "
                f"resetting to defaults",
                file=sys.stderr,
            )
            self.dim_weights = dict(DEFAULT_DIM_WEIGHTS)

        return self


# ═════════════════════════════════════════════════════════════
# Feedback aggregation (zero token, SQL only)
# ═════════════════════════════════════════════════════════════

def _aggregate_feedback(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Query user_feedback joined with market_news for analysis.

    Returns one row per unique news_url, with up/down counts, headline,
    source, and (when available) the AI score and summary from the report.

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
        for r in rows:
            net = r["up"] - r["down"]
            if net > 0:
                sentiment = "👍偏好"
            elif net < 0:
                sentiment = "👎排斥"
            else:
                sentiment = "⚖️中性"
            result.append({
                "headline": (r["headline"] or r["url"] or "?")[:60],
                "source": (r["source"] or "?")[:12],
                "up": r["up"],
                "down": r["down"],
                "sentiment": sentiment,
                "track_relevant": bool(r["track_relevant"]),
                "pos_label": (r["pos_label"] or ""),
                "neg_label": (r["neg_label"] or ""),
            })
        return result
    except Exception as e:
        print(f"  [WARN] Feedback aggregation failed: {e}", file=sys.stderr)
        return []


def _format_feedback_table(feedback: list[dict[str, Any]]) -> str:
    """Format aggregated feedback as a markdown table for the LLM prompt."""
    if not feedback:
        return "（暂无用户反馈数据）"

    lines = [
        "| 来源 | 👍 | 👎 | 倾向 | 赛道 | +标签 | -标签 | 新闻标题 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f in feedback:
        track = "🏷️" if f["track_relevant"] else ""
        pos = f.get("pos_label", "") or ""
        neg = f.get("neg_label", "") or ""
        lines.append(
            f"| {f['source']} | {f['up']} | {f['down']} "
            f"| {f['sentiment']} | {track} | {pos} | {neg} | {f['headline']} |"
        )
    return "\n".join(lines)


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
    except Exception:
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


def _format_params_for_prompt(params: dict[str, Any]) -> str:
    """Format current params as human-readable text for the LLM prompt."""
    topic_str = json.dumps(params.get("topic_boosts", {}), ensure_ascii=False)
    dim_str = json.dumps(params.get("dim_weights", {}), ensure_ascii=False)
    return (
        f"topic_boosts: {topic_str}\n"
        f"dim_weights: {dim_str}\n"
        f"当前版本: v{params.get('version', 0)}\n"
        f"上次校准摘要: {params.get('summary', '无')}"
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
    """Run the Calibrator: aggregate feedback → LLM analysis → persist params.

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

    # ── Gate: insufficient data ──
    if total_feedback < _MIN_FEEDBACK_THRESHOLD and not force:
        msg = (
            f"仅 {total_feedback} 条反馈，不足 {_MIN_FEEDBACK_THRESHOLD} 条阈值。"
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

    # ── Step 3: LLM analysis ──
    from src.agents.base import Agent

    agent = Agent(
        "calibrator",
        tools=None,
        model=None,
        max_tool_rounds=1,
        max_tokens=8192,
        output_schema=CalibratorOutput,
    )

    try:
        result = agent.run(
            feedback_days=days,
            feedback_table=_format_feedback_table(feedback),
            current_params=_format_params_for_prompt(current_params),
            start_date=start_date,
            end_date=end_date,
            total_feedback=total_feedback,
            unique_news=unique_news,
            _verbose=False,
        )
    except Exception as e:
        print(f"  [ERROR] Calibrator LLM call failed: {e}", file=sys.stderr)
        return {
            "skipped": True,
            "reason": f"LLM调用失败: {e}",
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
    except Exception:
        pass
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
        if topic in headline:
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
