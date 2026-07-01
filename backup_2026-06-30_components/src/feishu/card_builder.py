"""
Feishu card builder — helper functions for building interactive card elements.

Used to enrich the Briefer's LLM-generated card with interactive elements
that can't be expressed in markdown (action buttons, multi-column layouts, etc.).

Usage:
    from src.feishu.card_builder import build_news_feedback_actions

    feedback_row = build_news_feedback_actions("https://...", "2026-06-24")
"""

from __future__ import annotations

from typing import Any


def build_news_feedback_actions(news_url: str, target_date: str) -> dict[str, Any]:
    """Build a per-news feedback action row.

    Two buttons: 👍 有用 / 👎 没用.
    When clicked, sends a card.action.trigger event to the bot with the
    news URL so the counter can be incremented on the correct market_news row.

    Args:
        news_url: The URL of the news item (matches market_news.url).
        target_date: The report date (YYYY-MM-DD).

    Returns:
        Feishu action element dict.
    """
    return {
        "tag": "action",
        "layout": "bisected",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "👍 有用"},
                "type": "default",
                "value": json_dumps_compact({
                    "action": "news_feedback",
                    "type": "thumbs_up",
                    "news_url": news_url,
                    "target_date": target_date,
                }),
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "👎 没用"},
                "type": "default",
                "value": json_dumps_compact({
                    "action": "news_feedback",
                    "type": "thumbs_down",
                    "news_url": news_url,
                    "target_date": target_date,
                }),
            },
        ],
    }


def build_hot_topic_click_action(
    news_url: str, keyword: str, target_date: str
) -> dict[str, Any]:
    """Build a "感兴趣" click-tracking button for hot topic items.

    When clicked, sends a card.action.trigger event to the bot which records
    the click in user_feedback for feedback-loop keyword weight adjustment.

    Args:
        news_url: The URL of the hot topic news item.
        keyword: The associated hot keyword for feedback tracking.
        target_date: The report date (YYYY-MM-DD).

    Returns:
        Feishu action element dict.
    """
    return {
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "👀 感兴趣"},
                "type": "default",
                "value": json_dumps_compact({
                    "action": "hot_topic_click",
                    "news_url": news_url,
                    "keyword": keyword,
                    "target_date": target_date,
                }),
            },
        ],
    }


def json_dumps_compact(obj: dict[str, Any]) -> str:
    """JSON dumps without spaces (compact format for Feishu action values)."""
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
