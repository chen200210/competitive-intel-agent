"""Shared headline token extraction utilities.

Used by both the briefer pipeline (cross-day dedup) and the standalone
news scorer (same-day content dedup). Consolidates what were two diverged
implementations of the same core logic.

Originally:
  - _extract_content_tokens() in scripts/score_news.py
  - _headline_dedup_tokens() in src/agents/briefer.py
"""

from __future__ import annotations

import re

# ── Pre-compiled regex (hot path — called once per candidate) ──
_RE_GAME_NAMES = re.compile(r'《([^》]+)》')
_RE_CHINESE = re.compile(r'[一-鿿]+')
_RE_ENGLISH = re.compile(r'[A-Za-z]+')
_RE_TOPIC_WORDS = re.compile(r'[一-鿿\w]{2,}')  # combined CJK + word chars, min 2

# ── Keywords that should never count as content ──
_NOISE_BASIC = {
    "steam", "game", "play", "pc", "app",
}

_NOISE_CHINESE_GENERIC = {
    "报道", "文章", "分析", "认为", "表示", "显示", "数据",
    "目前", "已经", "可以", "进行", "一个", "这是", "这个",
    "其中", "通过", "以及", "包括", "对于", "根据", "作为",
    "不仅", "同时", "此外",
    "游戏", "玩家", "近日", "本周", "最新", "发布", "推出",
    "正式", "公布", "曝光", "介绍", "据悉", "了解",
    "陀螺周报", "独家", "资讯", "头条",
}


def extract_game_names(headline: str) -> set[str]:
    """Extract game names from 《》 brackets in a headline."""
    return set(_RE_GAME_NAMES.findall(headline))


def extract_topic_words(
    headline: str,
    *,
    min_chinese: int = 2,
    min_english: int = 3,
    noise: set[str] | None = None,
) -> set[str]:
    """Extract significant topic words from a headline.

    Args:
        headline: The headline text (game names in 《》 should be removed first).
        min_chinese: Minimum consecutive Chinese characters (default 2).
        min_english: Minimum consecutive English letters (default 3).
        noise: Extra words to exclude (merged with _NOISE_BASIC).

    Returns a set of topic words.
    """
    chinese = set(
        t for t in _RE_CHINESE.findall(headline)
        if len(t) >= min_chinese
    )
    english = set(
        t.lower() for t in _RE_ENGLISH.findall(headline)
        if len(t) >= min_english
    )

    excluded = _NOISE_BASIC | (noise or set())
    return (chinese | english) - excluded


def headline_dedup_tokens(headline: str) -> set[str]:
    """Extract normalized dedup tokens for cross-source/cross-day comparison.

    Used by briefer._headline_dedup_tokens — strips common prefixes,
    extracts game names + long topic words, lowercases everything.
    """
    clean = headline
    for prefix in ["游戏资讯", "行业活动", "行业分析", "[B站", "资讯"]:
        clean = clean.replace(prefix, "")

    games = extract_game_names(clean)
    words = extract_topic_words(
        clean,
        min_chinese=4,
        min_english=4,
        noise={"steam", "game", "play", "pc", "app"},
    )
    return {t.lower() for t in games | words}
