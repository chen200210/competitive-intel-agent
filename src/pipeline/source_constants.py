"""Canonical news source names and helpers.

All modules must reference these constants instead of hardcoding
Chinese/English source name strings. Adding a new source requires
changes ONLY in this file — no other module should need updating.

Usage:
    from src.pipeline.source_constants import NewsSource, is_bilibili, is_overseas

    # In scrapers (assign canonical name):
    item["source"] = NewsSource.GAME_17173

    # In consumers (check source type):
    if is_bilibili(item.get("source", "")):
        ...
    if is_overseas(item.get("source", "")):
        ...
"""

from __future__ import annotations


class NewsSource:
    """Canonical source name constants.

    Every scraper MUST write one of these exact values into the
    ``source`` column.  Consumers MUST use the helper functions
    below instead of substring-matching against these strings.
    """

    # ── Chinese game media ──
    GAME_17173: str = "17173"
    GAME_3DM: str = "3DM"
    GAME_TUOLUO: str = "游戏陀螺"
    GAME_RIBAO: str = "游戏日报"
    GAME_LOOK: str = "GameLook"

    # ── Overseas ──
    POCKET_GAMER: str = "pocketgamer.biz"

    # ── Video / UGC ──
    BILIBILI: str = "bilibili"

    # ── Hot Topic / Trending ──
    HOT_TOPIC: str = "hot_topic"


# ═══════════════════════════════════════════════════════════════════
# Source categories
# ═══════════════════════════════════════════════════════════════════

ALL_SOURCES: frozenset[str] = frozenset({
    NewsSource.GAME_17173,
    NewsSource.GAME_3DM,
    NewsSource.GAME_TUOLUO,
    NewsSource.GAME_RIBAO,
    NewsSource.GAME_LOOK,
    NewsSource.POCKET_GAMER,
    NewsSource.BILIBILI,
    NewsSource.HOT_TOPIC,
})

DOMESTIC_SOURCES: frozenset[str] = frozenset({
    NewsSource.GAME_17173,
    NewsSource.GAME_3DM,
    NewsSource.GAME_TUOLUO,
    NewsSource.GAME_RIBAO,
    NewsSource.GAME_LOOK,
})

OVERSEAS_SOURCES: frozenset[str] = frozenset({
    NewsSource.POCKET_GAMER,
})

# ═══════════════════════════════════════════════════════════════════
# Alias map — non-canonical variants → canonical name
# ═══════════════════════════════════════════════════════════════════

SOURCE_ALIASES: dict[str, str | None] = {
    # ── 3DM case / domain variants ──
    "3dm": NewsSource.GAME_3DM,
    "3dmgame": NewsSource.GAME_3DM,
    # ── 游戏陀螺 pinyin ──
    "youxituoluo": NewsSource.GAME_TUOLUO,
    # ── 游戏日报 abbreviation ──
    "yxrb": NewsSource.GAME_RIBAO,
    # ── GameLook case ──
    "gamelook": NewsSource.GAME_LOOK,
    # ── PocketGamer shorthand ──
    "pocketgamer": NewsSource.POCKET_GAMER,
    "pg.biz": NewsSource.POCKET_GAMER,
    # ── Bilibili domain / Chinese shorthand ──
    "bilibili.com": NewsSource.BILIBILI,
    "b站": NewsSource.BILIBILI,
    # ── Removed / discontinued sources → None ──
    "gamersky": None,
    "游侠": None,
    "游侠资讯": None,
    "ali213": None,
}

# ═══════════════════════════════════════════════════════════════════
# Source authority weights (0.0 – 1.0, higher = more authoritative)
# ═══════════════════════════════════════════════════════════════════

SOURCE_WEIGHTS: dict[str, float] = {
    NewsSource.GAME_17173: 0.45,
    NewsSource.GAME_3DM: 0.30,
    NewsSource.GAME_TUOLUO: 0.50,
    NewsSource.GAME_RIBAO: 0.35,
    NewsSource.GAME_LOOK: 0.40,
    NewsSource.POCKET_GAMER: 0.35,
    NewsSource.BILIBILI: 0.25,
}

# ═══════════════════════════════════════════════════════════════════
# Display ordering (lower = higher priority when tie-breaking)
# ═══════════════════════════════════════════════════════════════════

SOURCE_DISPLAY_ORDER: dict[str, int] = {
    NewsSource.GAME_TUOLUO: 0,
    NewsSource.GAME_LOOK: 1,
    NewsSource.GAME_RIBAO: 2,
    NewsSource.GAME_17173: 3,
    NewsSource.GAME_3DM: 4,
    NewsSource.POCKET_GAMER: 5,
    NewsSource.BILIBILI: 6,
}

# ═══════════════════════════════════════════════════════════════════
# Display labels (short human-readable forms for UI / logging)
# ═══════════════════════════════════════════════════════════════════

SOURCE_DISPLAY_LABELS: dict[str, str] = {
    NewsSource.GAME_17173: "17173",
    NewsSource.GAME_3DM: "3DM",
    NewsSource.GAME_TUOLUO: "游戏陀螺",
    NewsSource.GAME_RIBAO: "游戏日报",
    NewsSource.GAME_LOOK: "GameLook",
    NewsSource.POCKET_GAMER: "PG.biz",
    NewsSource.BILIBILI: "B站",
}

# ═══════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════


def normalize_source(raw: str | None) -> str | None:
    """Normalize a raw source name to its canonical form.

    Returns None when:
      * The input is empty / None
      * The source is a known removed / discontinued source

    Returns the raw string unchanged when the source is unrecognised
    (future-proof — new scrapers added before this module is updated).
    """
    if not raw:
        return None

    key = raw.strip().lower()
    if key in SOURCE_ALIASES:
        return SOURCE_ALIASES[key]

    # Exact-case hit against canonical set
    if raw in ALL_SOURCES:
        return raw

    # Case-insensitive fallback
    for canonical in ALL_SOURCES:
        if canonical.lower() == key:
            return canonical

    # Unknown → pass through (future-proof)
    return raw


def is_bilibili(source: str | None) -> bool:
    """Check whether *source* is B站 (bilibili)."""
    return normalize_source(source) == NewsSource.BILIBILI


def is_overseas(source: str | None) -> bool:
    """Check whether *source* is an overseas outlet."""
    return normalize_source(source) in OVERSEAS_SOURCES


def is_domestic(source: str | None) -> bool:
    """Check whether *source* is a domestic Chinese media outlet."""
    return normalize_source(source) in DOMESTIC_SOURCES


def is_valid_source(source: str | None) -> bool:
    """Check whether *source* is a recognised, active news source."""
    return normalize_source(source) in ALL_SOURCES


def source_weight(source: str | None) -> float:
    """Return the authority weight for *source* (0.0 for unknown)."""
    canonical = normalize_source(source)
    if canonical is None:
        return 0.0
    return SOURCE_WEIGHTS.get(canonical, 0.0)


def source_order(source: str | None) -> int:
    """Return the display priority for *source* (99 for unknown)."""
    canonical = normalize_source(source)
    if canonical is None:
        return 99
    return SOURCE_DISPLAY_ORDER.get(canonical, 99)


def source_label(source: str | None) -> str:
    """Return a human-readable display label for *source*."""
    canonical = normalize_source(source)
    if canonical is None:
        return source or "?"
    return SOURCE_DISPLAY_LABELS.get(canonical, canonical)
