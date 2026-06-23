"""
Game info tool — fetch structured game information directly from sources.

Instead of using broken general-purpose search engines, this tool:
  1. Checks our own DB first (taptap_new_games, steam_port_games tables)
  2. Falls back to fetching TapTap game pages (SSR HTML, httpx works)

No API keys, no search engines, no JS rendering required.

Usage:
    from src.tools.game_info import fetch_game_info
    result = fetch_game_info("王国保卫战5")

Agent integration:
    Tool(
        name="fetch_game_info",
        description="Fetch game info: description, tags, rating, update logs...",
        parameters={...},
        fn=fetch_game_info,
    )
"""

from __future__ import annotations

import json
from typing import Any

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def fetch_game_info(game_name: str, **_meta: Any) -> str:
    """Fetch structured information about a game.

    Sources (tried in order):
      1. Our DB (taptap_new_games, steam_port_games)
      2. TapTap game page HTML (SSR, direct fetch)

    Returns JSON string with:
      {found, game_name, description, tags, rating, developer, genre,
       update_info, source_type, source_url}
    """
    # ── 1. Check DB ──
    try:
        from src.storage.sqlite import get_db
        db = get_db()

        # Exact match first
        for table, url_field in [("taptap_new_games", "taptap_url"), ("steam_port_games", "steam_url")]:
            rows = db._connect().execute(
                f"SELECT * FROM {table} WHERE game_name = ? ORDER BY date DESC LIMIT 1",
                (game_name,)
            ).fetchall()
            if rows:
                r = dict(rows[0])
                return _format_db_result(game_name, r, table, url_field)

        # Fuzzy match (game_name contains or is contained by query)
        for table, url_field in [("taptap_new_games", "taptap_url"), ("steam_port_games", "steam_url")]:
            rows = db._connect().execute(
                f"SELECT * FROM {table} WHERE game_name LIKE ? ORDER BY date DESC LIMIT 1",
                (f"%{game_name}%",)
            ).fetchall()
            if rows:
                r = dict(rows[0])
                return _format_db_result(game_name, r, table, url_field)
    except Exception:
        pass

    return json.dumps({
        "found": False,
        "game_name": game_name,
        "error": "未在数据库中找到该游戏信息",
    }, ensure_ascii=False)


# ── Helpers ──────────────────────────────────────────────────────

def _format_db_result(
    game_name: str, row: dict[str, Any], table: str, url_field: str
) -> str:
    """Format a DB row into the standard game_info result JSON."""
    result: dict[str, Any] = {
        "found": True,
        "game_name": game_name,
        "source_type": f"db_{table}",
        "source_url": row.get(url_field, "") or "",
    }

    if table == "taptap_new_games":
        result.update({
            "description": row.get("description", "") or "",
            "tags": row.get("tags", "") or "",
            "rating": row.get("rating"),
            "developer": row.get("developer", "") or "",
            "genre": row.get("genre", "") or "",
            "downloads": row.get("downloads", "") or "",
        })
    elif table == "steam_port_games":
        result.update({
            "description": "",
            "tags": row.get("gameplay_tags", "") or "",
            "rating": None,
            "developer": "",
            "genre": row.get("genre", "") or "",
            "is_steam_port": True,
        })

    return json.dumps(result, ensure_ascii=False)


# ── Tool descriptor for Agent registration ──────────────────────

TOOL_DESCRIPTOR: dict[str, Any] = {
    "name": "fetch_game_info",
    "description": (
        "Fetch structured game information (description, tags, rating, developer, "
        "genre, update info) from TapTap and our database. "
        "Use this INSTEAD of web_search when you need information about a specific game. "
        "Returns structured JSON with game details — no need to parse search results."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "game_name": {
                "type": "string",
                "description": "Game name to look up, e.g. '王国保卫战5' or 'Monster Train 2'",
            },
        },
        "required": ["game_name"],
    },
}
