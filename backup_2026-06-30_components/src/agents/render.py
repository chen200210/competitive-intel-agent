"""
Card rendering — build Feishu card markdown sections in code (zero AI).

Three sections:
  1. 新游关注 — TapTap + Steam new games, merged display
  2. 排名变动 — track-relevant ranking changes table
  3. 市场变动 — per-news blocks with images + feedback buttons

All markdown is generated deterministically — no LLM hallucination risk.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from collections.abc import Callable
from typing import Any

from src.types import NewGameEntry, ChangeRecord, ScoredNewsItem, HotTopicItem
from src.agents.enrichment import enrich_news_images as _enrich_images
from src.feishu.card_builder import build_news_feedback_actions, build_hot_topic_click_action
from src.tools.url_utils import extract_domain


# ═════════════════════════════════════════════════════════════
# New games section
# ═════════════════════════════════════════════════════════════

def build_new_games_md(
    steam_ports: list[NewGameEntry],
    taptap_games: list[NewGameEntry],
    note: str = "",
) -> str:
    """Build the new-games section markdown in code. No LLM hallucination.

    Merges steam_ports and taptap_games into one list:
      - Games in BOTH: show TapTap entry with [Steam] badge
      - Steam-only: show as [Steam 移植]
      - TapTap-only: show normal TapTap entry
    """
    lines: list[str] = []

    if note:
        lines.append(note)
        lines.append("")

    # Build lookup sets
    steam_names = {s.get("game_name", "") for s in (steam_ports or [])}
    taptap_names = {t.get("game_name", "") for t in (taptap_games or [])}

    # ── Steam-only games (not in TapTap) ──
    steam_only = [s for s in (steam_ports or [])
                  if s.get("game_name", "") not in taptap_names]
    # ── Overlap games (in both) — shown via TapTap entry with [Steam] badge ──
    steam_overlap_names = steam_names & taptap_names

    if steam_only:
        for s in steam_only:
            name = s.get("game_name", "")
            url = s.get("steam_url", "") or s.get("url", "")
            lines.append(f"🔴 **{name}** [Steam 移植]")
            if url:
                lines.append(f"→ [Steam 主页]({url})")
            else:
                # No Steam URL — generate TapTap search link as fallback
                tap_search = f"https://www.taptap.cn/search/{urllib.parse.quote(name)}"
                lines.append(f"→ [TapTap 搜索]({tap_search})")
            lines.append("")
    elif not taptap_games:
        lines.append("（今日无新增Steam移植手游）")
        lines.append("")

    # ── TapTap games (exclude overlaps already shown as steam, unless they're in overlap) ──
    # Overlap games: show with [Steam] badge
    # Pure TapTap games: show normally
    shown_taptap = 0
    if taptap_games:
        for g in taptap_games:
            name = g.get("game_name", "")
            is_overlap = name in steam_overlap_names
            downloads = g.get("downloads", "")
            rating = g.get("rating") or ""
            tags = g.get("tags", "")
            taptap_url = g.get("taptap_url", "")

            badge = " [Steam]" if is_overlap else ""
            prefix = "🔴 " if is_overlap else ""
            lines.append(f"{prefix}**{name}**{badge} — {_summarize_tags(tags)}")
            detail = f"下载量 {downloads}"
            if rating:
                detail += f" | 评分 {rating}"
            if tags:
                detail += f" | {tags}"
            lines.append(detail)
            if taptap_url:
                lines.append(f"→ [TapTap 主页]({taptap_url})")
            lines.append("")
            shown_taptap += 1
    elif note:
        pass
    else:
        lines.append("（今日无新增TapTap赛道新游）")
        lines.append("")

    return "\n".join(lines)


def _summarize_tags(tags: str) -> str:
    """Make a short description from tags."""
    if not tags:
        return "新游"
    parts = [t.strip() for t in tags.split("|") if t.strip()]
    return "、".join(parts[:3]) + "游戏" if parts else "新游"


def _parse_downloads(dl_str: str) -> int:
    """Parse TapTap download string to a comparable integer.

    '18万+预约' → 180000, '5万+预约' → 50000, '6283预约' → 6283, '' → 0
    """
    dl_str = dl_str.strip()
    if not dl_str:
        return 0
    m = re.match(r'([\d.]+)\s*万', dl_str)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r'(\d+)', dl_str)
    if m:
        return int(m.group(1))
    return 0


# ═════════════════════════════════════════════════════════════
# Ranking section
# ═════════════════════════════════════════════════════════════

def _match_new_game(rank_name: str, yesterday_names: set[str]) -> bool:
    """Check if a ranking game name matches any of yesterday's new games.

    Delegates to the shared fuzzy_match_game_name() in taptap_resolver
    for the 3-strategy matching logic (exact → base name → containment).
    """
    try:
        from src.tools.taptap_resolver import fuzzy_match_game_name
        return fuzzy_match_game_name(rank_name, yesterday_names) is not None
    except ImportError as e:
        print(f"  [WARN] fuzzy_match_game_name unavailable: {e}", file=sys.stderr)
        return False


def build_ranking_md(
    changes: list[ChangeRecord],
    yesterday_new_games: set[str] | None = None,
) -> str:
    """Build the ranking section markdown in code. No LLM table hallucination.

    Args:
        changes: Rank change records from DB.
        yesterday_new_games: Game names that appeared as new games yesterday.
            Matched games get a 🔴【昨日新游】badge in the table.
    """
    lines: list[str] = []
    yday_names = yesterday_new_games or set()

    # ── Build TapTap URL lookup for games in changes ──
    taptap_urls = _get_taptap_urls([c.get("game_name", "") for c in changes])

    if changes:
        lines.append("| 游戏 | 榜单 | 今日排名 | 昨日排名 | 变化 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for c in changes[:12]:
            name = c.get("game_name", "")
            chart = c.get("chart_type", "")
            today = c.get("today_rank") or "出榜"
            yesterday = c.get("yesterday_rank") or "新进"
            rc = c.get("rank_change")
            if rc is not None and rc > 0:
                change_str = f"↓{rc}"
            elif rc is not None and rc < 0:
                change_str = f"↑{abs(rc)}"
            else:
                change_str = "—"

            # Link: prefer exact TapTap URL, fall back to TapTap search
            url = taptap_urls.get(name, "")
            if not url:
                url = f"https://www.taptap.cn/search/{urllib.parse.quote(name)}"

            # Tag yesterday's new games that entered the ranking today
            is_yesterday_new = _match_new_game(name, yday_names)
            if is_yesterday_new:
                name_cell = f"🔴【昨日新游】[**{name}**]({url})"
            else:
                name_cell = f"[**{name}**]({url})"

            lines.append(f"| {name_cell} | {chart} | {today} | {yesterday} | {change_str} |")
        lines.append("")
    else:
        lines.append("（今日无赛道排名变动）")

    return "\n".join(lines)


def _get_taptap_urls(game_names: list[str]) -> dict[str, str]:
    """Look up TapTap URLs for a list of ranking game names.

    Ranking names (diandian) differ from TapTap names — e.g.
    "策马守天关 - 三国塔防游戏" vs "策马守天关".  Uses multiple
    matching strategies in order:
      1. Exact match in taptap_new_games
      2. Base name extraction (text before " - " or "（")
      3. Substring containment (ranking name contains TapTap name)
      4. kv_cache lookup

    Returns name→url mapping.
    """
    urls: dict[str, str] = {}
    if not game_names:
        return urls

    try:
        from src.tools.taptap_resolver import resolve_taptap_urls
        urls = resolve_taptap_urls(game_names)
    except Exception as e:
        print(f"  [WARN] TapTap URL resolution failed: {e}", file=sys.stderr)

    return urls


# ═════════════════════════════════════════════════════════════
# Market section — per-news blocks with images + feedback
# ═════════════════════════════════════════════════════════════

def _split_markdown_entries(
    md: str,
    is_entry: "Callable[[str], bool]",
) -> tuple[str, list[str]]:
    """Split markdown by blank lines into header text + entry blocks.

    Blocks matching is_entry() are classified as entries; all others
    (including blank blocks) are header blocks joined back together.

    Args:
        md: Raw markdown string.
        is_entry: Predicate — given a stripped block, return True if it's an entry.

    Returns:
        (header: str, entries: list[str])
    """
    header_blocks: list[str] = []
    entry_blocks: list[str] = []

    for block in md.split("\n\n"):
        stripped = block.strip()
        if not stripped:
            continue
        if is_entry(stripped):
            entry_blocks.append(stripped)
        else:
            header_blocks.append(stripped)

    return "\n\n".join(header_blocks), entry_blocks


def build_market_elements(
    market_md: str,
    top_news: list[ScoredNewsItem],
    date: str = "",
) -> list[dict[str, Any]]:
    """Split market_md into per-news blocks and interleave images + feedback buttons.

    Each news entry gets its image (B站 cover or news og:image) inserted
    as an img element right after its markdown block, followed by per-news
    👍/👎 feedback buttons.

    Matches entries to top_news by extracting the URL from the markdown
    link ``→ [原文](url)`` and looking it up in top_news.

    Args:
        market_md: AI-generated market section markdown.
        top_news: Enriched news items with image_url and url fields.
        date: Report date (YYYY-MM-DD), used for feedback button target_date.

    Returns:
        List of card elements (markdown + optional img + feedback per entry).
    """
    # Pre-fill og:image for news items that don't have it yet
    _enrich_images(top_news)

    # Build URL → image_url lookup from top_news
    url_to_image: dict[str, str] = {}
    for item in top_news:
        img = item.get("image_url", "").strip()
        url = item.get("url", "").strip()
        if img and url:
            url_to_image[url] = img

    # Split market_md into blocks: header + per-entry sections
    # Entries are separated by blank lines and start with "> **"
    header, entry_blocks = _split_markdown_entries(
        market_md,
        is_entry=lambda s: s.startswith("> **"),
    )
    # Fallback: if no header blocks found, use first block of raw markdown
    if not header:
        header = market_md.split("\n\n")[0]

    elements: list[dict[str, Any]] = []
    elements.append({"tag": "markdown", "content": header})

    for block in entry_blocks:
        elements.append({"tag": "markdown", "content": block})

        # Extract URL from "→ [原文](url)" pattern
        url_match = re.search(r'→\s*\[原文\]\(([^)]+)\)', block)
        entry_url = url_match.group(1) if url_match else ""
        # Also try "→ [链接]" or just any markdown link
        if not entry_url:
            url_match = re.search(r'→\s*\[.*?\]\(([^)]+)\)', block)
            entry_url = url_match.group(1) if url_match else ""

        img_url = url_to_image.get(entry_url, "")
        if img_url:
            from src.feishu.pusher import upload_image  # lazy — avoids lark_oapi dep for non-push usage
            result = upload_image(img_url)
            if result.get("success"):
                elements.append({
                    "tag": "img",
                    "img_key": result["image_key"],
                    "alt": {"tag": "plain_text", "content": ""},
                })
                print(f"   [image] embedded for: {entry_url[:60]}...", file=sys.stderr)
            else:
                print(f"   [image] upload failed for {entry_url[:60]}: {result.get('error', 'unknown')}", file=sys.stderr)

        # Per-news feedback buttons (best-effort: skip if no URL to match)
        if entry_url and date:
            elements.append(build_news_feedback_actions(entry_url, date))

    return elements


# ═════════════════════════════════════════════════════════════
# Hot topics section
# ═════════════════════════════════════════════════════════════

def build_hot_topics_md(
    hot_items: list[HotTopicItem],
    keywords: list[str] | None = None,
) -> str:
    """Build the hot-topic section markdown in code (zero AI).

    Args:
        hot_items: Hot topic news items with headline, url, source,
                   snippet, keyword fields.
        keywords: Today's hot keywords for the header line.

    Returns:
        Markdown string for the Feishu card hot topics section.
    """
    lines: list[str] = []

    if not hot_items:
        lines.append("（今日无热点追踪内容 — 可能热点关键词采集或搜索失败）")
        return "\n".join(lines)

    # Keyword tags header
    if keywords:
        kw_tags = " · ".join(keywords[:5])
        lines.append(f"**今日热点关键词**：{kw_tags}")
        lines.append("")

    # Each hot item
    for i, item in enumerate(hot_items[:7]):  # max 7 hot items
        kw = item.get("keyword", "")
        headline = item.get("headline", "")
        snippet = item.get("snippet", "")
        ai_summary = item.get("ai_summary", "")
        url = item.get("url", "")
        source = item.get("source", "") or extract_domain(url)

        # Prefer AI summary over raw search snippet when available
        summary_text = ai_summary if ai_summary else snippet
        if len(summary_text) > 140:
            summary_text = summary_text[:140] + "..."

        kw_tag = f"[{kw}] " if kw else ""
        lines.append(f"**{i + 1}. {kw_tag}{headline}**")
        if summary_text:
            ai_tag = " 🤖" if ai_summary else ""
            lines.append(f"> {summary_text}{ai_tag}")
        if url:
            src_tag = f" · `{source}`" if source else ""
            lines.append(f"→ [原文]({url}){src_tag}")
        lines.append("")

    return "\n".join(lines)


def build_hot_topic_elements(
    hot_topics_md: str,
    hot_items: list[HotTopicItem],
    date: str = "",
) -> list[dict[str, Any]]:
    """Split hot_topics_md into per-item card elements with "感兴趣" buttons.

    Each hot topic item gets its own markdown block followed immediately by
    an "感兴趣" button — same per-item pattern as build_market_elements().

    Args:
        hot_topics_md: Hot topics section markdown.
        hot_items: Hot topic news items with url and keyword fields.
        date: Report date (YYYY-MM-DD), used for feedback target_date.

    Returns:
        List of card elements with per-item click tracking buttons.
    """
    # Build URL → keyword lookup from hot_items
    url_to_keyword: dict[str, str] = {}
    for item in hot_items:
        url = item.get("url", "").strip()
        kw = item.get("keyword", "").strip()
        if url:
            url_to_keyword[url] = kw

    # Split markdown into header + per-item entry blocks.
    # Entries are separated by blank lines and start with "**N. "
    header, entry_blocks = _split_markdown_entries(
        hot_topics_md,
        is_entry=lambda s: bool(re.match(r'^\*\*\d+\.\s', s)),
    )

    elements: list[dict[str, Any]] = []
    if header:
        elements.append({"tag": "markdown", "content": f"**🔥 热点追踪**\n\n{header}"})
    else:
        elements.append({"tag": "markdown", "content": "**🔥 热点追踪**"})

    # Per-item: markdown block → "感兴趣" button (max 7)
    for block in entry_blocks[:7]:
        elements.append({"tag": "markdown", "content": block})

        # Extract URL from "→ [原文](url)" pattern in the block
        url_match = re.search(r'→\s*\[原文\]\(([^)]+)\)', block)
        entry_url = (url_match.group(1) or "").strip() if url_match else ""

        keyword = url_to_keyword.get(entry_url, "")
        if entry_url and date:
            elements.append(build_hot_topic_click_action(entry_url, keyword, date))

    return elements
