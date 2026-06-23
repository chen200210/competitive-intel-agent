"""
Track Filter — pure rule engine for classifying games by track/ignore rules.

Zero AI token consumption. All game data flows through this module for
consistent classification before reaching any Agent.

Rules (in priority order):
  1. Track keywords match → 'track' (regardless of ignored category match)
  2. Ignored category keywords match → 'ignored' (only if rule 1 didn't fire)
  3. Neither → 'neutral'

Special rule:
  Steam-to-mobile ports are always 'track' when steam_port_always_include=true,
  regardless of genre matching.

Configuration is loaded from competitor_list.yaml → track_config section.

Usage:
    python -m src.pipeline.track_filter --test
    python -m src.pipeline.track_filter --game "明日方舟" --tags "塔防,二次元"
"""

from __future__ import annotations

import sys
from typing import Any


# ── Default keyword lists (fallback if YAML unavailable) ─────

DEFAULT_TRACK_KEYWORDS: list[str] = [
    # CN
    "塔防", "肉鸽",
    # EN
    "TD", "Tower Defense", "tower defense",
    "Roguelike", "roguelike", "Roguelite", "roguelite",
]

DEFAULT_IGNORED_KEYWORDS: list[str] = [
    "女性向", "二次元", "乙女",
]

# Track keywords with sub-keyword matching — e.g. "王国保卫战" matches "塔防"
# because it's a well-known TD game. These are brand/known-title signals.
TRACK_BRAND_SIGNALS: list[str] = [
    "王国保卫战", "Kingdom Rush", "保卫萝卜",
    "Dungeon Defense", "地牢防御",
]


def _load_track_config() -> dict[str, Any]:
    """Load track_config from competitor_list.yaml.

    Returns a dict with keys: genres, ignored_categories, track_overrides_ignore,
    steam_port_always_include. Falls back to defaults on error.
    """
    try:
        import yaml
        from src.config import settings
        yaml_path = settings.competitor_list_path
        if yaml_path.exists():
            with open(yaml_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
            return config.get("track_config", {})
    except Exception:
        pass
    return {}


def get_track_keywords() -> list[str]:
    """Return the active track keyword list, combining YAML config with defaults."""
    cfg = _load_track_config()
    genres = cfg.get("genres", [])
    if genres:
        # Combine YAML genres with default brand signals
        merged = list(genres)
        for kw in TRACK_BRAND_SIGNALS:
            if kw not in merged:
                merged.append(kw)
        return merged
    return DEFAULT_TRACK_KEYWORDS + TRACK_BRAND_SIGNALS


def get_ignored_keywords() -> list[str]:
    """Return the active ignored category keyword list."""
    cfg = _load_track_config()
    ignored = cfg.get("ignored_categories", [])
    return ignored if ignored else DEFAULT_IGNORED_KEYWORDS


def is_track_override_enabled() -> bool:
    """Check if track rules should override ignored category matches."""
    cfg = _load_track_config()
    return cfg.get("track_overrides_ignore", True)


def is_steam_port_always_include() -> bool:
    """Check if Steam ports should always be included regardless of track."""
    cfg = _load_track_config()
    return cfg.get("steam_port_always_include", True)


# ── Core classification logic ────────────────────────────────

def _keyword_in_text(keywords: list[str], text: str) -> bool:
    """Case-insensitive keyword match in text. Checks substrings."""
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    return False


def classify_game(
    game_name: str,
    genre: str = "",
    tags: list[str] | None = None,
    theme: str = "",
    developer: str = "",
    description: str = "",
    is_steam_port: bool = False,
) -> str:
    """Classify a game as 'track', 'ignored', or 'neutral'.

    Priority:
      1. Steam port + steam_port_always_include → 'track'
      2. Track keyword match → 'track' (regardless of ignored match)
      3. Ignored keyword match → 'ignored'
      4. Otherwise → 'neutral'

    Args:
        game_name: Game display name.
        genre: Primary genre string.
        tags: Tag list (e.g. from TapTap).
        theme: Theme/category string.
        developer: Developer name.
        description: Longer description text for deeper matching.
        is_steam_port: True if this is a Steam-to-mobile port.

    Returns:
        'track' | 'ignored' | 'neutral'
    """
    # ── Steam port special rule ──
    if is_steam_port and is_steam_port_always_include():
        return "track"

    # ── Build searchable text ──
    parts: list[str] = [game_name, genre, theme, developer, description]
    if tags:
        parts.extend(tags)
    combined = " ".join(p for p in parts if p)

    # ── Rule 1: Track keyword match (highest priority) ──
    track_keywords = get_track_keywords()
    if _keyword_in_text(track_keywords, combined):
        return "track"

    # ── Rule 2: Ignored category match (only if track didn't fire) ──
    ignored_keywords = get_ignored_keywords()
    if _keyword_in_text(ignored_keywords, combined):
        return "ignored"

    # ── Rule 3: Neutral ──
    return "neutral"


def should_include(game: dict[str, Any]) -> bool:
    """Check if a game dict should be included in the report.

    Returns True for 'track' and 'neutral' classifications.
    Returns False for 'ignored'.
    """
    classification = game.get("_track_classification", "")
    if not classification:
        # Try to classify on the fly
        classification = classify_game(
            game_name=game.get("game_name", ""),
            genre=game.get("genre", ""),
            tags=_parse_tags(game.get("tags", "")),
            theme=game.get("theme", ""),
            developer=game.get("developer", ""),
            description=game.get("description", ""),
            is_steam_port=game.get("is_steam_port", False),
        )
    return classification != "ignored"


def filter_games(games: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Classify a list of game dicts into three buckets.

    Each game dict gets a `_track_classification` field added in-place.

    Args:
        games: List of game dicts. Each should have at minimum:
               game_name, genre, tags, theme, developer, description, is_steam_port.

    Returns:
        {"track": [...], "ignored": [...], "neutral": [...]}
    """
    result: dict[str, list[dict[str, Any]]] = {
        "track": [],
        "ignored": [],
        "neutral": [],
    }
    for g in games:
        classification = classify_game(
            game_name=g.get("game_name", ""),
            genre=g.get("genre", ""),
            tags=_parse_tags(g.get("tags", "")),
            theme=g.get("theme", ""),
            developer=g.get("developer", ""),
            description=g.get("description", ""),
            is_steam_port=g.get("is_steam_port", False),
        )
        g["_track_classification"] = classification
        result[classification].append(g)
    return result


def _parse_tags(tags_value: str | list[str] | None) -> list[str]:
    """Parse tags from string (comma-separated) or list."""
    if tags_value is None:
        return []
    if isinstance(tags_value, list):
        return tags_value
    if isinstance(tags_value, str):
        return [t.strip() for t in tags_value.split(",") if t.strip()]
    return []


# ── CLI ───────────────────────────────────────────────────────

def _run_tests() -> int:
    """Built-in test suite. Returns number of failures."""
    failures = 0

    def check(name: str, expected: str, **kwargs: Any) -> None:
        nonlocal failures
        result = classify_game(**kwargs)
        status = "✓" if result == expected else "✗"
        if result != expected:
            failures += 1
        print(f"  [{status}] {name}: expected={expected}, got={result}")

    print("Track Filter Tests:\n")

    # ── Track matches ──
    check("TD game", "track", game_name="暗夜防线", genre="塔防", tags=["策略", "TD"])
    check("Roguelike game", "track", game_name="地牢探险", genre="Roguelike", tags=["肉鸽", "地牢"])
    check("Tower Defense EN", "track", game_name="Kingdom Rush", genre="Tower Defense")
    check("Brand signal", "track", game_name="王国保卫战", genre="策略")
    check("Sub-keyword in name", "track", game_name="保卫萝卜4", genre="休闲")

    # ── Track overrides ignored ──
    check("TD + anime (override)", "track",
          game_name="明日方舟", genre="塔防", tags=["塔防", "二次元"])
    check("Roguelike + anime (override)", "track",
          game_name="某肉鸽二次元", genre="Roguelike", tags=["肉鸽", "二次元"])

    # ── Ignored only ──
    check("otome game", "ignored", game_name="恋与制作人", genre="女性向", tags=["乙女", "恋爱"])
    check("anime game", "ignored", game_name="某二次元RPG", genre="RPG", tags=["二次元"])
    check("female-oriented", "ignored", game_name="闪耀暖暖", genre="换装", tags=["女性向"])

    # ── Neutral ──
    check("MOBA game", "neutral", game_name="王者荣耀", genre="MOBA", tags=["竞技", "5v5"])
    check("FPS game", "neutral", game_name="和平精英", genre="FPS", tags=["射击", "大逃杀"])
    check("no tags", "neutral", game_name="未知游戏")

    # ── Steam port special rule ──
    check("Steam port (no track match)", "track",
          game_name="某Steam移植游戏", genre="RPG", is_steam_port=True)
    check("Steam port + anime", "track",
          game_name="某Steam移植二次元", genre="RPG", tags=["二次元"], is_steam_port=True)

    # ── Edge cases ──
    check("Empty input", "neutral", game_name="")
    check("TD in developer name", "track", game_name="某游戏", developer="塔防工作室")

    print(f"\n{failures} failure(s)")
    return failures


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) >= 2 and sys.argv[1] == "--test":
        sys.exit(_run_tests())

    # ── Single game classification mode ──
    game_name = ""
    tags: list[str] = []
    genre = ""

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--game" and i + 1 < len(sys.argv):
            game_name = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--tags" and i + 1 < len(sys.argv):
            tags = [t.strip() for t in sys.argv[i + 1].split(",")]
            i += 2
        elif sys.argv[i] == "--genre" and i + 1 < len(sys.argv):
            genre = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if not game_name:
        print("Usage: python -m src.pipeline.track_filter --test")
        print("       python -m src.pipeline.track_filter --game <name> [--tags <t1,t2>] [--genre <g>]")
        sys.exit(1)

    result = classify_game(game_name=game_name, genre=genre, tags=tags)
    print(f"Game: {game_name}")
    print(f"Genre: {genre or '(none)'}")
    print(f"Tags: {tags or '(none)'}")
    print(f"Classification: {result}")
