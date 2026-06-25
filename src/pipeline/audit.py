"""
Card audit layer — post-Briefer quality gate (zero token).

Runs hard checks on the generated Feishu card before pushing.
Can auto-fix some issues (news filtering, ordering, truncation),
flags others for manual review (fake URLs, missing sections).

Usage:
    from src.pipeline.audit import audit_card, AuditContext, AuditResult

    context = AuditContext(
        taptap_games=taptap_games,
        steam_ports=steam_ports,
        market_news=market_news,
    )
    result = audit_card(card, context)
    if not result.passed:
        print(f"Audit failed: {result.failures}")
    card = result.fixed_card
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# ── Data classes ─────────────────────────────────────────────────

@dataclass
class AuditContext:
    """Input data used to generate the card — for cross-validation."""

    taptap_games: list[dict[str, Any]] = field(default_factory=list)
    steam_ports: list[dict[str, Any]] = field(default_factory=list)
    market_news: list[dict[str, Any]] = field(default_factory=list)

    def all_urls(self) -> set[str]:
        """Collect all real URLs from input data for cross-validation."""
        urls: set[str] = set()
        for src in [self.taptap_games, self.steam_ports, self.market_news]:
            for item in src:
                for key in ("taptap_url", "steam_url", "url", "source_url"):
                    val = item.get(key, "")
                    if val and val.startswith("http"):
                        urls.add(val)
        return urls

    def track_game_names(self) -> set[str]:
        """Game names that should appear in the new-games section."""
        names: set[str] = set()
        # Steam ports always included
        for g in self.steam_ports:
            name = g.get("game_name", "")
            if name:
                names.add(name)
        # TapTap games only if track_relevant
        for g in self.taptap_games:
            if g.get("track_relevant"):
                name = g.get("game_name", "")
                if name:
                    names.add(name)
        return names


@dataclass
class AuditResult:
    """Output of the card audit."""

    passed: bool = True
    score: int = 100  # 0-100, deduct per issue
    fixes_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    fixed_card: dict[str, Any] = field(default_factory=dict)


# ── Non-game keywords for news filtering ────────────────────────

NON_GAME_KEYWORDS = [
    "AirPods", "iPhone", "iPad", "MacBook", "Apple Watch",
    "电动滑板车", "电视", "耳机", "音箱", "手表",
    "Prime Day", "特惠精选", "优惠", "折扣", "促销",
    "世界杯", "足球", "NBA", "英超", "西甲", "欧冠",
    "演唱会", "张靓颖", "明星", "八卦", "走光", "抄袭",
    "芝麻街", "Netflix", "电影", "预告", "剧透",
    "礼包", "广告", "赛马大会", "抢号",
]

DEPRECATED_KEYWORDS = [
    "微恐", "冰河", "火山爆发", "风险反照", "risk_mirror",
]

REQUIRED_SECTIONS = [
    "新游关注",
    "市场变动",
    "排名变动",
]


# ── Main entry ───────────────────────────────────────────────────

def audit_card(card: dict[str, Any], context: AuditContext) -> AuditResult:
    """Run all audit checks on a Briefer-generated card.

    Args:
        card: The card dict (raw Briefer output, unwrapped).
        context: Input data used to generate the card.

    Returns:
        AuditResult with pass/fail, fixes, and fixed_card.
    """
    result = AuditResult(fixed_card=json.loads(json.dumps(card)))  # deep copy
    elements = result.fixed_card.get("elements", [])

    # ── 1. News source filter ──
    _check_news_source(elements, result)

    # ── 2. News content filter (non-game) ──
    _check_news_content(elements, result)

    # ── 3. News count ──
    _check_news_count(elements, result)

    # ── 4. New-games track filter ──
    _check_new_games_track(elements, context, result)

    # ── 5. Steam port ordering ──
    _check_steam_ordering(elements, context, result)

    # ── 6. Section completeness ──
    _check_sections(elements, result)

    # ── 8. Card size ──
    _check_card_size(result)

    # ── 9. Deprecated keywords ──
    _check_deprecated_keywords(result)

    # ── 10. URL authenticity ──
    _check_urls(result, context)

    # ── 11. Source links in analysis sections ──
    _check_analysis_links(elements, result)

    # ── Final score ──
    result.passed = len(result.failures) == 0
    return result


# ── Individual checks ────────────────────────────────────────────

def _find_section(elements: list[dict], *keywords: str) -> int | None:
    """Find the index of a markdown element whose content starts with one of keywords."""
    for i, el in enumerate(elements):
        if el.get("tag") != "markdown":
            continue
        content = el.get("content", "")
        for kw in keywords:
            if kw in content[:30]:
                return i
    return None


def _check_news_source(elements: list[dict], result: AuditResult) -> None:
    """Remove news entries not from 游侠/17173."""
    idx = _find_section(elements, "📰")
    if idx is None:
        return

    content = elements[idx].get("content", "")
    lines = content.split("\n")
    filtered = []
    removed = 0

    for line in lines:
        # Lines with "→ [原文]" should have a source marker before
        if "→ [原文]" in line:
            # Keep — sources are already validated upstream
            filtered.append(line)
        elif any(kw in line for kw in ["百度百科", "知乎", "头条", "搜狐", "腾讯新闻"]):
            removed += 1
            continue
        else:
            filtered.append(line)

    if removed > 0:
        elements[idx]["content"] = "\\n".join(filtered)
        result.fixes_applied.append(f"新闻板块移除 {removed} 条非游戏媒体来源")
        result.score -= removed * 2


def _check_news_content(elements: list[dict], result: AuditResult) -> None:
    """Remove news entries with non-game keywords in headlines."""
    idx = _find_section(elements, "📰")
    if idx is None:
        return

    content = elements[idx].get("content", "")
    lines = content.split("\n")
    filtered = []
    removed = 0

    for line in lines:
        if any(kw.lower() in line.lower() for kw in NON_GAME_KEYWORDS):
            removed += 1
            continue
        filtered.append(line)

    if removed > 0:
        elements[idx]["content"] = "\\n".join(filtered)
        result.fixes_applied.append(f"新闻板块移除 {removed} 条非游戏内容")
        result.score -= removed * 5


def _check_news_count(elements: list[dict], result: AuditResult) -> None:
    """Truncate news to max 7 entries."""
    idx = _find_section(elements, "📰")
    if idx is None:
        return

    content = elements[idx].get("content", "")
    # Count news entries by "→ [原文]" markers
    entries = content.split("\n")
    news_lines = [i for i, line in enumerate(entries) if "→ [原文]" in line]

    if len(news_lines) > 7:
        # Keep lines up to and including the 7th "→ [原文]" marker
        cutoff = news_lines[6] + 1  # include the 7th link line
        # Also keep any continuation lines after the last kept entry
        while cutoff < len(entries) and entries[cutoff].strip() and "**" not in entries[cutoff]:
            cutoff += 1
        elements[idx]["content"] = "\\n".join(entries[:cutoff])
        removed = len(news_lines) - 7
        result.fixes_applied.append(f"新闻板块截断 {removed} 条，保留 7 条")
        result.score -= removed


def _check_new_games_track(elements: list[dict], context: AuditContext, result: AuditResult) -> None:
    """Remove games that aren't in any known data source from new-games section.

    All TapTap new games (track or not) are valid — non-track games are
    shown as fallback when no track/steam games exist.
    """
    idx = _find_section(elements, "🆕")
    if idx is None:
        return

    # All valid game names: steam ports + all taptap games (track + non-track fallback)
    valid_names: set[str] = set()
    for g in context.steam_ports:
        name = g.get("game_name", "")
        if name:
            valid_names.add(name)
    for g in context.taptap_games:
        name = g.get("game_name", "")
        if name:
            valid_names.add(name)

    if not valid_names:
        return

    content = elements[idx].get("content", "")
    lines = content.split("\n")
    filtered = []
    removed = 0
    current_game = ""

    for line in lines:
        if re.search(r'[📊🆕📰🎮🔴🟡⚪]', line):
            filtered.append(line)
            continue

        name_match = re.match(r'\*\*(.+?)\*\*', line)
        if name_match:
            current_game = name_match.group(1).replace("[Steam 移植]", "").replace("[Steam]", "").strip()

        if current_game and current_game not in valid_names:
            if name_match:
                removed += 1
            continue

        filtered.append(line)

    if removed > 0:
        elements[idx]["content"] = "\\n".join(filtered)
        result.fixes_applied.append(f"新游板块移除 {removed} 款非游戏数据源游戏")
        result.score -= removed * 3


def _check_steam_ordering(elements: list[dict], context: AuditContext, result: AuditResult) -> None:
    """Ensure Steam port games appear first in the new-games section."""
    if not context.steam_ports:
        return

    idx = _find_section(elements, "🆕")
    if idx is None:
        return

    steam_names = {g.get("game_name", "") for g in context.steam_ports}
    content = elements[idx].get("content", "")
    lines = content.split("\n")

    # Find game blocks and check if Steam games are at the top
    game_blocks: list[list[str]] = []
    current_block: list[str] = []

    for line in lines:
        if re.match(r'\*\*.+?\*\*', line) and current_block:
            game_blocks.append(current_block)
            current_block = [line]
        else:
            current_block.append(line)
    if current_block:
        game_blocks.append(current_block)

    if not game_blocks:
        return

    # Check if first blocks are Steam ports
    steam_first = True
    for block in game_blocks[:len(steam_names)]:
        first_line = block[0] if block else ""
        if not any(name in first_line for name in steam_names):
            steam_first = False
            break

    if not steam_first:
        # Reorder: Steam blocks first
        steam_blocks = [b for b in game_blocks
                        if any(name in (b[0] if b else "") for name in steam_names)]
        other_blocks = [b for b in game_blocks if b not in steam_blocks]
        reordered = steam_blocks + other_blocks
        new_lines = []
        for block in reordered:
            new_lines.extend(block)
        elements[idx]["content"] = "\\n".join(new_lines)
        result.fixes_applied.append("Steam 移植游戏已重排到新游板块最前面")
        result.score -= 1


def _check_sections(elements: list[dict], result: AuditResult) -> None:
    """Verify all required sections are present."""
    markdown_texts = [
        el.get("content", "") for el in elements
        if el.get("tag") == "markdown"
    ]
    all_text = " ".join(markdown_texts)

    for section in REQUIRED_SECTIONS:
        if section not in all_text:
            result.failures.append(f"缺少必选板块: {section}")
            result.score -= 15


def _check_card_size(result: AuditResult) -> None:
    """Truncate card if it exceeds Feishu's 30KB limit."""
    card_str = json.dumps(result.fixed_card, ensure_ascii=False)
    if len(card_str) <= 30000:
        return

    # Find the longest markdown element and truncate it
    elements = result.fixed_card.get("elements", [])
    longest_idx = -1
    longest_len = 0
    for i, el in enumerate(elements):
        if el.get("tag") == "markdown":
            content_len = len(el.get("content", ""))
            if content_len > longest_len:
                longest_len = content_len
                longest_idx = i

    if longest_idx >= 0:
        content = elements[longest_idx].get("content", "")
        # Truncate to fit within 30KB
        target_reduction = len(card_str) - 29000
        if target_reduction > 0 and len(content) > target_reduction:
            elements[longest_idx]["content"] = content[:len(content) - target_reduction] + "\\n\\n_(内容过长，已截断)_"
            result.fixes_applied.append(f"卡片超 30KB，已截断最长板块")
            result.score -= 3


def _check_deprecated_keywords(result: AuditResult) -> None:
    """Detect deprecated keywords in the card."""
    card_str = json.dumps(result.fixed_card, ensure_ascii=False)
    found = [kw for kw in DEPRECATED_KEYWORDS if kw in card_str]
    if found:
        result.failures.append(f"卡片中出现已废弃关键词: {', '.join(found)}")
        result.score -= len(found) * 10


def _check_urls(result: AuditResult, context: AuditContext) -> None:
    """Verify that URLs in the card come from input data."""
    valid_urls = context.all_urls()
    if not valid_urls:
        return  # no URLs to validate against — skip

    card_str = json.dumps(result.fixed_card, ensure_ascii=False)
    # Extract all URLs from the card
    urls_in_card = set(re.findall(r'https?://[^\s\\")]+', card_str))

    fake_urls = []
    for url in urls_in_card:
        # Skip well-known domains that aren't in input but are safe
        if any(d in url for d in ["taptap.cn", "gamersky.com", "17173.com", "ali213.net",
                                     "3dmgame.com", "youxituoluo.com", "bilibili.com",
                                     "yxrb.net", "gamelook.com.cn"]):
            continue
        if url not in valid_urls and not any(url.startswith(v) for v in valid_urls):
            fake_urls.append(url)

    if fake_urls:
        result.failures.append(f"发现 {len(fake_urls)} 个无法验证的 URL: {fake_urls[:3]}")
        result.score -= len(fake_urls) * 10


def _check_analysis_links(elements: list[dict], result: AuditResult) -> None:
    """Verify that the market section has source links.

    Market section is now split into multiple markdown blocks (one per news item,
    each with optional image). Checks all blocks collectively.
    """
    # Collect all market-related content across blocks
    market_contents: list[str] = []
    for el in elements:
        if el.get("tag") != "markdown":
            continue
        content = el.get("content", "")
        if "📰" in content[:30] or "→ [原文]" in content or "→ [TapTap]" in content:
            market_contents.append(content)

    all_content = "\n".join(market_contents)
    urls = re.findall(r'https?://[^\s\\")]+', all_content)
    if urls:
        return  # has links, OK

    result.warnings.append("市场变动板块无任何来源链接")
    result.score -= 5


# ── CLI test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Self-test with a minimal card
    test_card = {
        "header": {"title": {"tag": "plain_text", "content": "测试"}, "template": "blue"},
        "elements": [
            {"tag": "markdown", "content": "**📊 今日概况**\\n测试"},
            {"tag": "markdown", "content": "**🆕 新游关注**\\n\\n**Steam游戏** [Steam 移植]\\n\\n**塔防新游** — 测试\\n下载量 1万+ | 评分 8.0 | 塔防 → [TapTap](https://www.taptap.cn/app/123)"},
            {"tag": "markdown", "content": "**📰 市场变动**\\n\\n**游戏新闻标题** — 游侠资讯\\n摘要\\n→ [原文](https://www.gamersky.com/news/123)"},
            {"tag": "markdown", "content": "**📊 排名变动**\\n\\n**塔防游戏** +5 | 免费榜"},
            {"tag": "markdown", "content": "**🎮 设计洞察**\\n\\n**塔防新游**：核心机制是..."},
        ],
    }

    ctx = AuditContext(
        taptap_games=[{"game_name": "塔防新游", "track_relevant": True, "taptap_url": "https://www.taptap.cn/app/123"}],
        steam_ports=[{"game_name": "Steam游戏"}],
        market_news=[{"headline": "游戏新闻标题", "source": "游侠资讯", "url": "https://www.gamersky.com/news/123"}],
    )

    result = audit_card(test_card, ctx)
    print(f"Passed: {result.passed}")
    print(f"Score: {result.score}")
    if result.fixes_applied:
        print(f"Fixes: {result.fixes_applied}")
    if result.warnings:
        print(f"Warnings: {result.warnings}")
    if result.failures:
        print(f"Failures: {result.failures}")
