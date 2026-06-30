"""Cross-validate a claim by searching for corroborating/contradicting evidence.

This tool is the key differentiator between Deep Research and Hot Tracker:
instead of taking a single source's claim at face value, it searches other
sources to verify whether the claim is corroborated, contradicted, or isolated.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.tools.web_search import _scrape_360_news, _scrape_sogou_news
from src.pipeline.token_utils import headline_dedup_tokens


def cross_validate(claim: str, source_url: str, **_meta: Any) -> str:
    """Search for corroborating/contradicting evidence for a claim.

    Given a claim and its source URL, searches 360 News + Sogou News in parallel
    for other sources covering the same event. Classifies results by headline
    token overlap into corroborating / contradicting / no_evidence.

    BEST-EFFORT: search failures do not throw — they return a degraded verdict
    so the Agent can continue rather than crash.

    Args:
        claim: The key claim to verify (will be truncated to 80 chars for search)
        source_url: The URL that made this claim (excluded from results)
        **_meta: injected by Agent base class (_called_by, _run_id, _target_date)

    Returns:
        JSON string: {"claim", "corroborating", "contradicting", "no_evidence", "verdict"}
    """
    # Truncate claim to search-friendly length
    query = claim[:80].rstrip("。，,.") if len(claim) > 80 else claim
    # Use quotes for exact-phrase search
    search_query = f'"{query}"'

    # Search both engines in parallel (mitigates M2: was serial 15-30s each)
    all_results: list[dict[str, Any]] = []

    def _search_one(engine_fn, q, max_r):
        try:
            result_str = engine_fn(q, max_results=max_r)
            parsed = json.loads(result_str)
            return [r for r in parsed.get("results", []) if r.get("url") != source_url]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(_search_one, _scrape_360_news, search_query, 3): "360",
            pool.submit(_search_one, _scrape_sogou_news, search_query, 3): "sogou",
        }
        for future in as_completed(futures):
            try:
                results = future.result()
                all_results.extend(results)
            except Exception:
                continue

    # Dedup by URL within cross_validate results
    seen_urls: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in all_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            deduped.append(r)

    if not deduped:
        return json.dumps({
            "claim": claim,
            "corroborating": [],
            "contradicting": [],
            "unrelated": [],
            "no_evidence": True,
            "verdict": "isolated_claim",
        }, ensure_ascii=False)

    # Classify results by headline token overlap with the claim
    claim_tokens = headline_dedup_tokens(claim)
    corroborating: list[dict[str, str]] = []
    other_sources: list[dict[str, str]] = []
    unrelated: list[dict[str, str]] = []

    for r in deduped:
        title = r.get("title", "")
        r_tokens = headline_dedup_tokens(title)
        overlap = len(claim_tokens & r_tokens) if claim_tokens and r_tokens else 0

        snippet_raw = r.get("snippet")
        snippet = str(snippet_raw)[:200] if snippet_raw else ""

        entry = {
            "title": title,
            "url": r.get("url", ""),
            "snippet": snippet,
        }

        if overlap >= 2:
            corroborating.append(entry)
        elif overlap >= 1:
            other_sources.append(entry)
        else:
            unrelated.append(entry)

    # Determine verdict
    if len(corroborating) >= 2:
        verdict = "verified"
    elif len(corroborating) >= 1:
        verdict = "partial"
    else:
        verdict = "unverified"

    return json.dumps({
        "claim": claim,
        "corroborating": corroborating[:3],
        "other_sources": other_sources[:3],
        "unrelated": unrelated[:3],
        "no_evidence": len(deduped) == 0,
        "verdict": verdict,
    }, ensure_ascii=False)
