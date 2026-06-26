"""
Feishu bot — WebSocket long connection, handles card action callbacks (feedback 👍/👎).

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

# ── Monkey-patch: SDK types action.value as Dict but Feishu sends JSON string ──
from lark_oapi.event.callback.model.p2_card_action_trigger import CallBackAction as _CB_Action
_CB_Action._types["value"] = str

from src.config import settings

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════
# Card action handler — feedback buttons (👍 / 👎)
# ═════════════════════════════════════════════════════════════

def _handle_card_action(event_obj: Any) -> None:
    """Handle card.action.trigger events (button clicks on interactive cards)."""
    try:
        print(f"\n[CARD_ACTION] Received event: {type(event_obj).__name__}")

        event = getattr(event_obj, 'event', None)
        print(f"[CARD_ACTION] event type: {type(event).__name__ if event else 'None'}")
        action_attr = getattr(event, 'action', None)
        action_value = getattr(action_attr, 'value', None) if action_attr else None
        print(f"[CARD_ACTION] value: {repr(action_value)[:200]}")

        if not action_value:
            print("[CARD_ACTION] No action_value — skipping")
            return

        # action_value might be dict, JSON string, or double-encoded JSON string
        if isinstance(action_value, str):
            try:
                action_value = json.loads(action_value)
                # Feishu sometimes double-encodes: parse again if still a string
                if isinstance(action_value, str):
                    action_value = json.loads(action_value)
            except Exception as e:
                print(f"[CARD_ACTION] Failed to parse action_value JSON: {e}")
                return

        if not isinstance(action_value, dict):
            print(f"[CARD_ACTION] Unexpected action_value type: {type(action_value)}")
            return

        action = action_value.get("action", "")
        print(f"[CARD_ACTION] action={action}")

        # Extract chat_id from event (common to all card actions)
        chat_id = ""
        for attr_name in ['chat_id', 'open_chat_id', 'open_id', 'user_id']:
            for obj in [event, getattr(event, 'context', None), getattr(event, 'host', None)]:
                if obj:
                    val = getattr(obj, attr_name, None)
                    if val:
                        print(f"[CARD_ACTION] Found {attr_name}={val} on {type(obj).__name__}")
                        if not chat_id and attr_name in ('chat_id', 'open_chat_id'):
                            chat_id = val

        if not chat_id:
            print("[CARD_ACTION] No chat_id found — skipping")
            return

        print(f"[CARD_ACTION] chat_id={chat_id}")

        if action == "news_feedback":
            fb_type = action_value.get("type", "")
            target_date = action_value.get("target_date", "")
            news_url = action_value.get("news_url", "")
            # Extract user identity from event operator
            operator = getattr(event, 'operator', None)
            user_open_id = getattr(operator, 'open_id', '') if operator else ''
            _handle_news_feedback(fb_type, target_date, news_url, chat_id, user_open_id)
        elif action == "hot_topic_click":
            news_url = action_value.get("news_url", "")
            keyword = action_value.get("keyword", "")
            target_date = action_value.get("target_date", "")
            operator = getattr(event, 'operator', None)
            user_open_id = getattr(operator, 'open_id', '') if operator else ''
            _handle_hot_topic_click(target_date, news_url, keyword, chat_id, user_open_id)
        else:
            print(f"[CARD_ACTION] Unknown action: {action}")

    except Exception as e:
        logger.error(f"Card action processing error: {e}")


def _handle_news_feedback(fb_type: str, target_date: str, news_url: str, chat_id: str,
                          user_open_id: str = "") -> None:
    """Handle per-news feedback button clicks (👍 / 👎).

    One feedback per user per news URL. Increments counter on market_news
    and logs to user_feedback for audit trail.
    """
    from src.storage.sqlite import get_db

    try:
        db = get_db()

        # Look up headline for the reply message
        headline = ""
        rows = db._connect().execute(
            "SELECT headline FROM market_news WHERE url = ? AND date = ?",
            (news_url, target_date),
        ).fetchall()
        if rows:
            headline = rows[0]["headline"]
            import re
            headline = re.sub(r'^(游戏资讯|行业活动|行业分析)\s*', '', headline)

        result = db.increment_news_feedback(
            url=news_url, date=target_date,
            feedback_type=fb_type, chat_id=chat_id,
            open_id=user_open_id,
        )

        user_name = _get_user_name(user_open_id)

        if result == "duplicate":
            _reply_text("你已对此新闻反馈过了，感谢参与 🙏", chat_id)
        elif result == "inserted":
            if fb_type == "thumbs_up":
                msg = f"感谢 {user_name} 的反馈，接下来将会推荐更多类似「{headline}」的新闻 🙏" if headline else \
                      f"感谢 {user_name} 的反馈，接下来将会推荐更多类似新闻 🙏"
            else:
                msg = f"感谢 {user_name} 的反馈，接下来将会减少类似「{headline}」的新闻 🙏" if headline else \
                      f"感谢 {user_name} 的反馈，接下来将会减少类似新闻 🙏"
            _reply_text(msg, chat_id)
        else:
            _reply_text("感谢反馈！🙏", chat_id)
    except Exception as e:
        logger.error(f"Failed to save news feedback: {e}")
        _reply_text("感谢反馈！🙏", chat_id)


def _handle_hot_topic_click(
    target_date: str, news_url: str, keyword: str, chat_id: str, user_open_id: str = ""
) -> None:
    """Handle hot topic "感兴趣" button clicks.

    Records the click in user_feedback for feedback-loop keyword weight adjustment.
    One click per user per news URL (deduped).
    """
    from datetime import date as _date
    from src.storage.sqlite import get_db

    try:
        db = get_db()
        today = _date.today().isoformat()
        result = db.record_hot_topic_click(
            date=today,
            target_date=target_date,
            news_url=news_url,
            keyword=keyword,
            chat_id=chat_id,
            open_id=user_open_id,
        )

        if result == "duplicate":
            _reply_text("你已对此热点反馈过了，感谢参与 🙏", chat_id)
        else:
            keyword_display = f"「{keyword}」" if keyword else ""
            _reply_text(
                f"已记录你对{keyword_display}相关热点的兴趣，我们会持续优化推荐 🙏",
                chat_id,
            )
    except Exception as e:
        logger.error(f"Failed to record hot topic click: {e}")
        _reply_text("感谢反馈！🙏", chat_id)


# ── User name cache ──────────────────────────────────────────────

_name_cache: dict[str, str] = {}


def _get_user_name(open_id: str) -> str:
    """Look up user display name via Feishu contact API. Cached, best-effort."""
    if not open_id:
        return "匿名用户"
    if open_id in _name_cache:
        return _name_cache[open_id]

    try:
        from lark_oapi.api.contact.v3 import GetUserRequest
        from lark_oapi.api.contact.v3.resource.user import User as UserResource
        from lark_oapi.core.model.config import Config

        conf = Config()
        conf.app_id = settings.feishu_app_id
        conf.app_secret = settings.feishu_app_secret
        req = GetUserRequest.builder() \
            .user_id_type("open_id") \
            .user_id(open_id) \
            .build()
        resp = UserResource(conf).get(req)
        if resp.success():
            name = resp.data.user.name
            if name:
                _name_cache[open_id] = name
                return name
    except Exception as e:
        print(f"  [WARN] Failed to look up user name for {open_id[:12]}: {e}", file=sys.stderr)
        pass
    fallback = f"用户{open_id[:6]}"
    _name_cache[open_id] = fallback
    return fallback


# ── Reply helper ──────────────────────────────────────────────────

def _reply_text(text: str, chat_id: str) -> None:
    from src.feishu.pusher import push_text
    push_text(text, chat_id)


# ── Message receiver (text messages are ignored; bot only handles card actions) ──

def _process_message(text: str, chat_id: str) -> None:
    """Text messages are ignored — bot only responds to card button clicks."""
    pass


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
    ).register_p2_im_message_receive_v1(on_receive_message) \
     .register_p2_card_action_trigger(_handle_card_action) \
     .build()

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
