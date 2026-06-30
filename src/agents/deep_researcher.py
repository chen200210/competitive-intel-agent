"""Deep Research Agent — multi-step autonomous research with adversarial verification.

Given an open question, the Agent:
1. Decomposes it into 3-4 sub-questions
2. Searches each sub-question in parallel (web_search tool)
3. Fetches full body text when snippets are insufficient (web_fetch tool)
4. Cross-validates 3-5 key claims against other sources (cross_validate tool)
5. Synthesizes a 500-word cited brief with verified/partial/unverified markers

Entry point: run_deep_research(question, date, ...)
Called by: runner.py (manual CLI) or bot.py (auto-trigger via ≥3 clicks)
"""

from __future__ import annotations

import json
import sys
from datetime import date as _date
from typing import Any

from pydantic import BaseModel

from src.agents.base import Agent, Tool
from src.storage.sqlite import get_db


# ═══════════════════════════════════════════════════════════════
# Pydantic output schema
# ═══════════════════════════════════════════════════════════════

class _Citation(BaseModel):
    url: str
    title: str
    verified: bool = False
    claim: str = ""


class _DeepResearchOutput(BaseModel):
    """Validated output from the Deep Research Agent."""
    report_md: str                              # 400-600 word cited markdown report
    citations: list[_Citation] = []             # all sources cited
    key_findings: list[str] = []                # 3-5 concise bullets
    confidence: str = "medium"                  # high | medium | low


# ═══════════════════════════════════════════════════════════════
# Tool wrappers
# ═══════════════════════════════════════════════════════════════

def _web_search(query: str, max_results: int = 5, **_kw: Any) -> str:
    """Search 360 News + Sogou News for recent Chinese news about a topic.

    Returns JSON with title, url, snippet for each result.
    Use this for each sub-question — you can issue multiple calls in parallel.
    """
    if not query:
        return json.dumps({"error": "empty query"}, ensure_ascii=False)
    try:
        from src.tools.web_search import web_search
        return web_search(query, max_results=max_results)
    except Exception as e:
        return json.dumps({"error": str(e)[:200]}, ensure_ascii=False)


def _web_fetch(url: str, **_kw: Any) -> str:
    """Fetch the body text of a web page (~500 chars).

    Use this when a search result's snippet is too short to extract useful
    information. Do NOT call for every result — only for promising URLs.
    """
    if not url:
        return json.dumps({"error": "empty URL"}, ensure_ascii=False)
    try:
        from src.agents.enrichment import fetch_article_body
        body = fetch_article_body(url, timeout=10)
        if not body:
            return json.dumps(
                {"status": "empty", "hint": "page returned no readable text"},
                ensure_ascii=False,
            )
        return json.dumps(
            {"status": "ok", "text": body[:600]},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)[:200]}, ensure_ascii=False)


def _cross_validate(claim: str, source_url: str, **_kw: Any) -> str:
    """Verify a claim by searching other sources for corroborating/contradicting evidence.

    Use this on 3-5 key claims before writing the final report.
    Returns JSON with corroborating URLs, contradicting URLs, and a verdict.
    """
    if not claim:
        return json.dumps({"error": "empty claim"}, ensure_ascii=False)
    try:
        from src.tools.cross_validate import cross_validate
        return cross_validate(claim, source_url)
    except Exception as e:
        return json.dumps(
            {"claim": claim, "error": str(e)[:200], "verdict": "error"},
            ensure_ascii=False,
        )


# ── Tool descriptors for Agent registration ──

WEB_SEARCH_TOOL = Tool(
    name="web_search",
    description=(
        "Search 360 News + Sogou News for recent Chinese news. "
        "Use this to find information about each decomposed sub-question. "
        "You can issue multiple web_search calls in parallel. "
        "Returns a list of results with title, url, and snippet for each."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query. Be specific — use quotes for exact phrases.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (default 5).",
            },
        },
        "required": ["query"],
    },
    fn=_web_search,
)

WEB_FETCH_TOOL = Tool(
    name="web_fetch",
    description=(
        "Fetch the body text of a web page (~500 chars). "
        "Use this when a search result's snippet is too short to extract "
        "useful information. Only call for promising URLs — do NOT fetch every result."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL to fetch.",
            },
        },
        "required": ["url"],
    },
    fn=_web_fetch,
)

CROSS_VALIDATE_TOOL = Tool(
    name="cross_validate",
    description=(
        "Verify a key claim by searching other sources for corroborating or "
        "contradicting evidence. Use this on 3-5 important claims before "
        "writing the final report. Returns corroborating URLs, contradicting "
        "URLs, and a verdict (verified/partial/unverified/isolated_claim)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "claim": {
                "type": "string",
                "description": "The key claim to verify (max 80 chars used for search).",
            },
            "source_url": {
                "type": "string",
                "description": "The URL that made this claim (excluded from validation results).",
            },
        },
        "required": ["claim", "source_url"],
    },
    fn=_cross_validate,
)


# ═══════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════

def run_deep_research(
    question: str,
    date: str | None = None,
    push_chat_id: str | None = None,
    verbose: bool = False,
    source_hot_topic_url: str = "",
    triggered_by: str = "manual",
    original_keyword: str = "",
) -> dict[str, Any]:
    """Run the Deep Research Agent on a topic question.

    Called by:
      - runner.py for manual CLI: --deep-research "AI+游戏 2026趋势"
      - bot.py for auto-trigger: 热点"感兴趣" ≥3 clicks

    Args:
        question: The research question to investigate (may be enriched from keyword)
        date: Date string YYYY-MM-DD (defaults to today)
        push_chat_id: Feishu chat ID for pushing results (optional)
        verbose: Print diagnostic output to stderr
        source_hot_topic_url: The hot_topic_news.url that triggered this (auto only)
        triggered_by: 'manual' | 'auto'
        original_keyword: The original short keyword before enrichment,
            used for matching user click records (auto only). Defaults to question.

    Returns:
        Dict with keys: success, report_md, citations, key_findings, confidence,
        topic, date, report_id, push_success, _timing, error (if failed)
    """
    if date is None:
        date = _date.today().isoformat()

    # The keyword for matching click records (auto-trigger path needs the raw keyword)
    lookup_keyword = original_keyword or question

    db = get_db()

    # ── Idempotency check (M1) ──
    existing = db.get_deep_research_report(date, question)
    if existing:
        if verbose:
            print(f"  [DR] Report already exists for '{question}' on {date}, returning cached.",
                  file=sys.stderr)
        return {
            "success": True,
            "cached": True,
            "topic": question,
            "date": date,
            "report_md": existing["report_md"],
            "citations": json.loads(existing["citations_json"] or "[]"),
            "key_findings": json.loads(existing["sub_questions_json"] or "[]"),
            "confidence": existing.get("confidence", "medium"),
            "report_id": existing["id"],
            "push_success": bool(existing.get("pushed", False)),
        }

    # ── Load today's market_news headlines for dedup (M3) ──
    # Deep Research should be aware of what the daily brief already covered,
    # so it can focus on NEW information rather than rehashing.
    market_headlines: list[str] = []
    try:
        market_news = db.get_market_news_by_date(date)
        for mn in market_news:
            headline = (mn.get("headline") or "").strip()
            if headline:
                market_headlines.append(headline)
    except Exception as e:
        if verbose:
            print(f"  [DR] Failed to load market news for dedup: {e}", file=sys.stderr)
        # best-effort — dedup is a quality optimization, not a hard requirement

    # Build a compact context string for the prompt.
    # Empty → Agent works normally; non-empty → Agent sees what's already covered.
    if market_headlines:
        # Truncate to avoid blowing up the user prompt (roughly 3 KB max).
        headlines_preview = "\n".join(
            f"  - {h[:100]}" for h in market_headlines[:30]
        )
        market_context = (
            f"今日日报已覆盖以下新闻（共 {len(market_headlines)} 条），"
            f"你的深度研究报告应聚焦**新信息**：\n{headlines_preview}"
        )
    else:
        market_context = "（今日无日报市场新闻，无需考虑去重）"

    # ── Build the Agent ──
    agent = Agent(
        "deep_research",
        tools=[WEB_SEARCH_TOOL, WEB_FETCH_TOOL, CROSS_VALIDATE_TOOL],
        max_tool_rounds=8,
        max_tokens=16384,
        output_schema=_DeepResearchOutput,
    )

    # ── Run ──
    if verbose:
        print(f"  [DR] Starting Deep Research: '{question}'", file=sys.stderr)
        if market_headlines:
            print(f"  [DR] Loaded {len(market_headlines)} market news headlines for dedup",
                  file=sys.stderr)

    try:
        result = agent.run(
            question=question,
            market_context=market_context,
            _verbose=verbose,
        )
    except Exception as e:
        if verbose:
            print(f"  [DR] Agent.run() failed: {e}", file=sys.stderr)
        return {
            "success": False,
            "topic": question,
            "date": date,
            "error": str(e),
        }

    # ── Extract validated output ──
    report_md = result.get("report_md", "")
    citations = result.get("citations", [])
    key_findings = result.get("key_findings", [])
    confidence = result.get("confidence", "medium")

    # Normalize citations (may be dicts or _Citation instances)
    citations_list: list[dict[str, Any]] = []
    for c in citations:
        if isinstance(c, dict):
            citations_list.append(c)
        elif hasattr(c, "model_dump"):
            citations_list.append(c.model_dump())
        else:
            citations_list.append({"url": str(c), "title": "", "verified": False, "claim": ""})

    # ── Persist to DB ──
    try:
        report_id = db.insert_deep_research_report(
            date=date,
            topic=question,
            sub_questions_json=json.dumps(key_findings, ensure_ascii=False),
            report_md=report_md,
            citations_json=json.dumps(citations_list, ensure_ascii=False),
            source_hot_topic_url=source_hot_topic_url,
            triggered_by=triggered_by,
            chat_id=push_chat_id or "",
            confidence=confidence,
        )
    except Exception as e:
        if verbose:
            print(f"  [DR] DB insert failed: {e}", file=sys.stderr)
        report_id = 0

    # ── Push to Feishu if requested ──
    push_success = False
    if push_chat_id and report_md:
        try:
            from src.feishu.pusher import push_deep_research_with_mentions

            # Build a simple card from the report
            card = _build_deep_research_card(
                topic=question,
                report_md=report_md,
                citations=citations_list,
                confidence=confidence,
                date=date,
            )

            # Get clickers if auto-triggered — use original keyword (stored in user_feedback)
            mention_ids: list[str] = []
            if triggered_by == "auto":
                lookup_keyword = original_keyword or question
                mention_ids = db.get_topic_clickers(lookup_keyword, date)

            push_result = push_deep_research_with_mentions(
                card, push_chat_id, mention_ids,
                mention_text=f"「{question}」深度研究报告已生成",
            )

            push_success = bool(push_result.get("success"))
            if push_success:
                if report_id:
                    db.mark_deep_research_pushed(report_id)
                if verbose:
                    print(f"  [DR] Pushed to {push_chat_id}", file=sys.stderr)
            else:
                if verbose:
                    print(f"  [DR] Push failed: {push_result.get('error', 'unknown')}", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"  [DR] Push exception: {e}", file=sys.stderr)

    return {
        "success": True,
        "cached": False,
        "topic": question,
        "date": date,
        "report_md": report_md,
        "citations": citations_list,
        "key_findings": key_findings,
        "confidence": confidence,
        "report_id": report_id,
        "push_success": push_success,
        "_timing": result.get("_timing"),
    }


# ═══════════════════════════════════════════════════════════════
# Card builder (simple — reuse renders for complex cards later)
# ═══════════════════════════════════════════════════════════════

def _build_deep_research_card(
    topic: str,
    report_md: str,
    citations: list[dict[str, Any]],
    confidence: str,
    date: str,
) -> dict[str, Any]:
    """Build a Feishu interactive card for a Deep Research report.

    This is a simple first version. Future iterations can add feedback
    buttons and richer formatting.
    """
    confidence_label = {"high": "🟢 高", "medium": "🟡 中", "low": "🔴 低"}.get(
        confidence, "🟡 中"
    )

    # Build citation links
    citation_lines: list[str] = []
    for i, c in enumerate(citations[:10], 1):
        url = c.get("url", "")
        title = c.get("title", "") or url
        verified = "✅" if c.get("verified") else "⏳"
        if url:
            citation_lines.append(f"{i}. {verified} [{title}]({url})")
        else:
            citation_lines.append(f"{i}. {verified} {title}")

    citations_md = "\n".join(citation_lines) if citation_lines else "（无引用）"

    # Truncate report_md for the card body (Feishu has length limits)
    body = report_md[:3000] if len(report_md) > 3000 else report_md

    card_md = (
        f"**{topic}**\n\n"
        f"{body}\n\n"
        f"---\n"
        f"**置信度**: {confidence_label}\n"
        f"**日期**: {date}\n"
        f"**引用来源**:\n{citations_md}\n"
    )

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🔬 深度研究: {topic[:30]}"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "markdown",
                "content": card_md,
            },
            {
                "tag": "hr",
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": (
                            f"🤖 Deep Research Agent · {date} · "
                            f"置信度 {confidence} · "
                            f"{len(citations)} 个来源"
                        ),
                    }
                ],
            },
        ],
    }
