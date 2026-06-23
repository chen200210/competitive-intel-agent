"""
Feishu card builder — helper functions for building interactive card elements.

Used to enrich the Briefer's LLM-generated card with interactive elements
that can't be expressed in markdown (action buttons, multi-column layouts, etc.).

Usage:
    from src.feishu.card_builder import build_diandian_search_action

    action_element = build_diandian_search_action("怪物火车2")
"""

from __future__ import annotations

from typing import Any


def build_diandian_search_action(game_name: str) -> dict[str, Any]:
    """Build a Feishu action element with a "🔍 查点点数据" button.

    When clicked, the button sends a card.action.trigger event to the bot
    with the game_name in the action value.

    Args:
        game_name: Name of the game to search on Diandian Data.

    Returns:
        Feishu action element dict.
    """
    return {
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {
                    "tag": "plain_text",
                    "content": "🔍 查点点数据",
                },
                "type": "default",
                "value": json_dumps_compact({"action": "diandian_search", "game_name": game_name}),
            }
        ],
    }


def build_new_game_card_entry(
    game_name: str,
    downloads: str = "",
    rating: float | None = None,
    tags: str = "",
    taptap_url: str = "",
    has_bundle_id: bool = False,
) -> str:
    """Build a markdown string for a single new game entry in the card.

    Args:
        game_name: Game display name.
        downloads: Download count string (e.g. "10万+").
        rating: Rating score (e.g. 8.5).
        tags: Comma-separated or pipe-separated tag string.
        taptap_url: TapTap page URL.
        has_bundle_id: Whether the game has a bundle_id (enables diandian search).

    Returns:
        Markdown string for the game entry.
    """
    parts = [f"**{game_name}**"]

    # Info line
    info_parts = []
    if downloads:
        info_parts.append(f"下载量 {downloads}")
    if rating is not None:
        info_parts.append(f"评分 {rating}")
    if tags:
        info_parts.append(tags.replace("|", "、"))
    if info_parts:
        parts.append(" | ".join(info_parts))

    # Links
    links = []
    if taptap_url:
        links.append(f"→ [TapTap]({taptap_url})")
    if has_bundle_id:
        links.append("🔍 查点点数据")

    if links:
        parts.append(" ".join(links))

    return "\\n".join(parts)


def enrich_card_with_diandian_actions(
    card: dict[str, Any],
    taptap_games: list[dict[str, Any]],
) -> dict[str, Any]:
    """Inject diandian search buttons after the new-games section.

    Uses URL buttons — clicking opens diandian search in browser directly.
    No bot callback required.

    Args:
        card: The card dict from Briefer.
        taptap_games: TapTap new games list (from DB).

    Returns:
        The enriched card dict (mutated in place).
    """
    elements = card.get("elements", [])
    if not elements:
        return card

    # Find games with bundle_ids AND mentioned in the card's new-games section
    new_games_idx = None
    new_games_content = ""
    for i, el in enumerate(elements):
        if el.get("tag") == "markdown" and "🆕" in el.get("content", "")[:30]:
            new_games_idx = i
            new_games_content = el.get("content", "")
            break

    if new_games_idx is None:
        return card

    searchable = []
    for g in taptap_games:
        name = g.get("game_name", "")
        if name and g.get("bundle_id") and name in new_games_content:
            searchable.append({"name": name, "url": f"https://app.diandian.com/search?keyword={name}"})

    # Build button rows (2 per action element)
    buttons = []
    for s in searchable:
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"🔍 {s['name'][:8]}"},
            "type": "default",
            "value": {"action": "diandian_search", "game_name": s["name"]},
        })

    # Insert action elements after the new-games section (2 buttons per row)
    insert_idx = new_games_idx + 1
    for i in range(0, len(buttons), 2):
        row_buttons = buttons[i:i+2]
        action_el = {
            "tag": "action",
            "layout": "bisected",
            "actions": row_buttons,
        }
        elements.insert(insert_idx, action_el)
        insert_idx += 1

    card["elements"] = elements
    return card


def json_dumps_compact(obj: dict[str, Any]) -> str:
    """JSON dumps without spaces (compact format for Feishu action values)."""
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
