"""
Feishu bot — WebSocket long connection, receive @messages, intent routing.

Uses lark-oapi SDK builder pattern to register event handlers.

Usage:
    python -m src.feishu.bot
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import lark_oapi as lark
from lark_oapi.ws import Client as WSClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from src.config import settings

logger = logging.getLogger(__name__)

# ── Intent recognition ──────────────────────────────────────────

INTENT_PROMPT = """判断用户意图，返回 JSON：
{"intent": "query_history"|"deep_research"|"compare"|"casual_chat",
 "entities": {"products": ["产品名"], "time_range": "today"|"this_week"|"last_week"|"this_month"},
 "needs_live_search": true|false,
 "search_queries": ["自动生成的搜索词"]}
用户问题："""


def _classify_intent(text: str) -> dict[str, Any]:
    from openai import OpenAI
    client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    try:
        response = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": INTENT_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content or "{}")
    except Exception:
        return {"intent": "casual_chat", "entities": {}}


# ── Reply ───────────────────────────────────────────────────────

def _reply_text(text: str, chat_id: str) -> None:
    from src.feishu.pusher import push_text
    push_text(text, chat_id)


# ── Intent handlers ─────────────────────────────────────────────

def _handle_query_history(entities: dict[str, Any], chat_id: str) -> None:
    products = entities.get("products", [])
    if not products:
        _reply_text("请告诉我你想查哪款游戏，比如「原神上周表现怎么样」", chat_id)
        return
    from src.storage.sqlite import get_db
    db = get_db()
    lines = []
    for product in products[:3]:
        history = db.get_game_history(bundle_id=product, days=14)
        if not history:
            rows = db._connect().execute(
                "SELECT bundle_id FROM rankings WHERE game_name LIKE ? LIMIT 1",
                (f"%{product}%",)
            ).fetchall()
            if rows:
                history = db.get_game_history(bundle_id=rows[0]["bundle_id"], days=14)
        if history:
            ranks = [h["rank"] for h in history[-7:]]
            trend = "上升" if len(ranks) >= 2 and ranks[-1] < ranks[0] else \
                    "下降" if len(ranks) >= 2 and ranks[-1] > ranks[0] else "稳定"
            lines.append(
                f"**{product}** 近 7 天排名: {' → '.join(str(r) for r in ranks)}\n"
                f"趋势: {trend}，当前第 {ranks[-1]} 位"
            )
        else:
            lines.append(f"**{product}**: 未找到数据")
    _reply_text("\n\n".join(lines), chat_id)


def _handle_deep_research(entities: dict[str, Any], chat_id: str) -> None:
    products = entities.get("products", [])
    queries = entities.get("search_queries", [])
    if not products:
        _reply_text("请告诉我你想调研哪款游戏", chat_id)
        return
    _reply_text(f"🔍 正在调研「{products[0]}」相关信息，可能需要 1-2 分钟...", chat_id)
    try:
        from src.agents.researcher import research
        result = research(
            change={
                "game_name": products[0], "bundle_id": "", "developer": "",
                "today_rank": None, "yesterday_rank": None,
                "rank_change": None, "change_type": "manual_query",
                "date": "", "platform": "iOS",
            },
            context_from_scanner=queries[0] if queries else "用户手动追问",
        )
        findings = result.get("findings", [])
        if findings:
            lines = [f"**{products[0]} 调研结果**\n"]
            for f in findings[:5]:
                lines.append(f"• {f.get('headline', '')}")
                for s in f.get("sources", [])[:2]:
                    lines.append(f"  📎 [{s.get('title', '来源')[:30]}]({s.get('url', '')})")
            _reply_text("\n".join(lines), chat_id)
        else:
            _reply_text(f"调研完成但未找到关于「{products[0]}」的具体信息。", chat_id)
    except Exception as e:
        _reply_text(f"调研出错: {e}", chat_id)


def _handle_compare(entities: dict[str, Any], chat_id: str) -> None:
    products = entities.get("products", [])
    if len(products) < 2:
        _reply_text("请提供至少两款游戏名称，如「对比原神和鸣潮最近一周的表现」", chat_id)
        return
    from src.storage.sqlite import get_db
    db = get_db()
    lines = ["**对比分析**\n"]
    for product in products[:3]:
        history = db.get_game_history(bundle_id=product, days=7)
        if history:
            ranks = [h["rank"] for h in history]
            lines.append(f"**{product}**: {' → '.join(str(r) for r in ranks)} (当前第{ranks[-1]}位)")
        else:
            rows = db._connect().execute(
                "SELECT bundle_id FROM rankings WHERE game_name LIKE ? LIMIT 1",
                (f"%{product}%",)
            ).fetchall()
            if rows:
                history = db.get_game_history(bundle_id=rows[0]["bundle_id"], days=7)
                ranks = [h["rank"] for h in history]
                lines.append(f"**{product}**: {' → '.join(str(r) for r in ranks)} (当前第{ranks[-1]}位)")
            else:
                lines.append(f"**{product}**: 未找到数据")
    _reply_text("\n".join(lines), chat_id)


# ── Message processor ───────────────────────────────────────────

def _process_message(text: str, chat_id: str) -> None:
    """Process an incoming text message and reply."""
    if not text or text in ("你好", "hi", "hello"):
        _reply_text(
            "你好！我是竞品情报助手。\n\n你可以：\n"
            "• 📊 查历史 — **原神上周表现怎么样**\n"
            "• 🔍 做调研 — **为什么XX今天突然冲榜**\n"
            "• ⚖️ 做对比 — **对比原神和鸣潮**\n\n试试问我点什么？",
            chat_id,
        )
        return

    logger.info(f"Message: '{text[:80]}' (chat: {chat_id[:12]}...)")
    intent_data = _classify_intent(text)
    intent = intent_data.get("intent", "casual_chat")
    entities = intent_data.get("entities", {})
    logger.info(f"Intent: {intent}")

    handlers = {
        "query_history": _handle_query_history,
        "deep_research": _handle_deep_research,
        "compare": _handle_compare,
    }
    handler = handlers.get(intent)
    if handler:
        handler(entities, chat_id)
    else:
        _reply_text(
            "我是竞品情报助手，可以帮你：\n"
            "• 📊 查历史 — **原神上周表现怎么样**\n"
            "• 🔍 做调研 — **为什么XX今天突然冲榜**\n"
            "• ⚖️ 做对比 — **对比原神和鸣潮**\n\n试试问我点什么？",
            chat_id,
        )


# ── Event handler function ──────────────────────────────────────

def on_receive_message(event_obj: Any) -> None:
    """Handle im.message.receive_v1 event. Called by SDK dispatcher."""
    try:
        # SDK passes P2ImMessageReceiveV1 object — extract event data
        event = getattr(event_obj, 'event', None)
        if event is None:
            logger.warning(f"No event data in: {type(event_obj)}")
            return

        message = getattr(event, 'message', None)
        if message is None:
            return

        chat_id = getattr(message, 'chat_id', '')
        msg_type = getattr(message, 'message_type', '')

        if msg_type != "text":
            return

        content_str = getattr(message, 'content', '{}')
        content = json.loads(content_str)
        text = content.get("text", "")

        # Strip @mentions
        mentions = getattr(message, 'mentions', []) or []
        for m in mentions:
            name = getattr(m, 'name', '')
            if name:
                text = text.replace(f"@{name}", "").strip()

        # Print sender info
        sender = getattr(event, 'sender', None)
        sender_id = getattr(sender, 'sender_id', None)
        open_id = getattr(sender_id, 'open_id', 'unknown') if sender_id else 'unknown'
        print(f"\n📨 Chat ID: {chat_id}")
        print(f"   Sender: {open_id}")
        print(f"   Text: {text[:100]}")

        if text:
            _process_message(text, chat_id)

    except Exception as e:
        logger.error(f"Event processing error: {e}")


# ── Main ────────────────────────────────────────────────────────

def start_bot() -> None:
    """Start the Feishu bot with WebSocket long connection."""
    print("🤖 Starting Feishu bot (WebSocket)...")
    print(f"   App ID: {settings.feishu_app_id[:8]}...")

    # Build event handler with registered message receiver
    handler = EventDispatcherHandler()
    handler = handler.builder(
        encrypt_key="",
        verification_token=settings.feishu_verification_token,
    ).register_p2_im_message_receive_v1(on_receive_message).build()

    ws = WSClient(
        app_id=settings.feishu_app_id,
        app_secret=settings.feishu_app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )

    print("   Bot is running. Press Ctrl+C to stop.")
    ws.start()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    start_bot()
