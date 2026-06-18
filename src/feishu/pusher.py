"""
Feishu message pusher — send interactive cards to chats/users.

Uses lark-oapi SDK to obtain tenant_access_token and call message API.

Usage:
    from src.feishu.pusher import push_card
    push_card(card_json, chat_id="oc_xxx")
"""

from __future__ import annotations

import json
import logging
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateMessageResponse,
)

from src.config import settings

logger = logging.getLogger(__name__)


# ── Token management ────────────────────────────────────────────

def _get_client() -> lark.Client:
    """Create a Lark client with app credentials."""
    return lark.Client.builder() \
        .app_id(settings.feishu_app_id) \
        .app_secret(settings.feishu_app_secret) \
        .build()


# ── Send message ────────────────────────────────────────────────

def send_message(
    content: str,
    msg_type: str = "interactive",
    chat_id: str | None = None,
    *,
    receive_id: str | None = None,
    receive_id_type: str = "chat_id",
) -> dict[str, Any]:
    """Send a message to a Feishu chat or user.

    Args:
        content: Message content string (JSON for interactive cards, text otherwise).
        msg_type: "interactive" for card JSON, "text" for plain text.
        chat_id: Target chat_id. If None, uses receive_id.
        receive_id: Target receive_id (user open_id, chat_id, etc.).
        receive_id_type: "chat_id" or "open_id" or "user_id".

    Returns:
        dict with success/error and message_id if successful.
    """
    client = _get_client()

    target = chat_id or receive_id
    if not target:
        return {"error": "No target specified — provide chat_id or receive_id"}

    request = CreateMessageRequest.builder() \
        .receive_id_type(receive_id_type) \
        .request_body(
            CreateMessageRequestBody.builder()
            .content(content)
            .msg_type(msg_type)
            .receive_id(target)
            .build()
        ) \
        .build()

    try:
        response: CreateMessageResponse = client.im.v1.message.create(request)
        if response.success():
            return {
                "success": True,
                "message_id": response.data.message_id,
            }
        else:
            return {
                "error": f"Feishu API error: code={response.code}, msg={response.msg}",
            }
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return {"error": str(e)}


def push_card(
    card: dict[str, Any],
    chat_id: str,
) -> dict[str, Any]:
    """Push an interactive card to a Feishu chat.

    Args:
        card: The card dict — can be the raw card body {header, elements, ...}
              or a wrapper {msg_type, card: {...}}.
        chat_id: Feishu chat_id (e.g., "oc_xxx").

    Returns:
        dict with success/error.
    """
    # Unwrap if caller passed {msg_type, card} wrapper
    if "card" in card and "header" not in card:
        card = card["card"]
    content = json.dumps(card, ensure_ascii=False)
    return send_message(content, msg_type="interactive", chat_id=chat_id)


def push_text(
    text: str,
    chat_id: str,
) -> dict[str, Any]:
    """Push a plain text message to a Feishu chat."""
    content = json.dumps({"text": text}, ensure_ascii=False)
    return send_message(content, msg_type="text", chat_id=chat_id)


# ── Utility ─────────────────────────────────────────────────────

def get_chat_list() -> list[dict[str, Any]]:
    """List chats the bot has access to."""
    client = _get_client()
    from lark_oapi.api.im.v1 import ListChatRequest, ListChatResponse
    request = ListChatRequest.builder().build()
    try:
        response: ListChatResponse = client.im.v1.chat.list(request)
        if response.success() and response.data.items:
            return [
                {"chat_id": c.chat_id, "name": c.name or "(unnamed)"}
                for c in response.data.items
            ]
        elif response.data:
            items = getattr(response.data, 'items', None) or []
            return [{"chat_id": getattr(c, 'chat_id', ''), "name": getattr(c, 'name', '')} for c in items]
        return [
            {"error": f"code={response.code}, msg={response.msg}",
             "hint": "可能需要开通 im:chat 权限"}
        ]
    except Exception as e:
        return [{"error": str(e)}]


def find_user_by_email(email: str) -> dict[str, Any]:
    """Find a user's open_id by email. Use this to get a target for private messages.

    Returns dict with open_id or error.
    """
    client = _get_client()
    from lark_oapi.api.contact.v3 import BatchGetIdUserRequest, BatchGetIdUserRequestBody
    try:
        request = BatchGetIdUserRequest.builder().request_body(
            BatchGetIdUserRequestBody.builder()
            .emails([email])
            .build()
        ).build()
        response = client.contact.v3.user.batch_get_id(request)
        if response.success() and response.data.user_list:
            for u in response.data.user_list:
                if u.email == email:
                    return {
                        "email": email,
                        "user_id": u.user_id,
                        "open_id": u.open_id,
                        "union_id": u.union_id,
                    }
        return {"error": f"User not found: {email}"}
    except Exception as e:
        return {"error": str(e)}


def push_to_user(card: dict[str, Any], open_id: str) -> dict[str, Any]:
    """Push an interactive card to a user's private chat."""
    if "card" in card and "header" not in card:
        card = card["card"]
    content = json.dumps(card, ensure_ascii=False)
    return send_message(content, msg_type="interactive",
                        receive_id=open_id, receive_id_type="open_id")


def push_daily_card(
    chat_id: str,
    date: str | None = None,
) -> dict[str, Any]:
    """Push the daily report card for a given date to a Feishu chat.

    Reads the Briefer card JSON from the analysis_reports table.
    If date is None, uses the latest available date.
    """
    from src.storage.sqlite import get_db
    db = get_db()

    if date is None:
        dates = db.get_available_dates()
        if not dates:
            return {"error": "No data in database"}
        date = dates[0]

    report = db.get_analysis_report(date)
    if not report or not report.get("brief_card_json"):
        return {"error": f"No brief card found for {date}. Run the pipeline first."}

    try:
        card_data = json.loads(report["brief_card_json"])
        card = card_data.get("card", card_data)
        return push_card(card, chat_id)
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse card JSON: {e}"}


def upload_image(image_url: str) -> dict[str, Any]:
    """Download an image from URL and upload to Feishu. Returns {image_key, ...}."""
    import httpx

    try:
        resp = httpx.get(image_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()

        image_bytes = resp.content
        content_type = "image/png"

        # Detect and convert WebP/unsupported formats to JPEG
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(image_bytes))
            fmt = img.format
            if fmt and fmt.upper() not in ("PNG", "JPEG", "GIF", "JPG"):
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=85)
                image_bytes = buf.getvalue()
                content_type = "image/jpeg"
        except Exception:
            pass  # keep original bytes if conversion fails

        # Get tenant access token
        token_resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
            timeout=15,
        )
        token = token_resp.json().get("tenant_access_token", "")

        # Upload via raw API (SDK's CreateImage has compatibility issues)
        upload_resp = httpx.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            files={"image": ("image", image_bytes, content_type)},
            data={"image_type": "message"},
            timeout=30,
        )
        result = upload_resp.json()
        if result.get("code") == 0:
            return {"success": True, "image_key": result["data"]["image_key"]}
        else:
            return {"error": f"Feishu API: code={result.get('code')}, msg={result.get('msg')}"}
    except Exception as e:
        return {"error": str(e)}


def upload_images_for_card(
    card: dict[str, Any],
    image_urls: list[str],
) -> dict[str, Any]:
    """Download images from URLs, upload to Feishu, embed into card.

    Replaces placeholder img tags in the card with real Feishu image_keys.
    """
    if not image_urls:
        return card

    uploaded_keys: list[str] = []
    for url in image_urls[:3]:  # max 3 images per card
        result = upload_image(url)
        if result.get("success"):
            uploaded_keys.append(result["image_key"])

    if not uploaded_keys:
        return card

    # Add images to the top of the card elements (after header)
    elements = card.get("elements", [])
    for key in uploaded_keys:
        elements.insert(0, {
            "tag": "img",
            "img_key": key,
            "alt": {"tag": "plain_text", "content": ""},
        })

    card["elements"] = elements
    return card


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def _test_card() -> dict[str, Any]:
        return {
            "header": {
                "title": {"tag": "plain_text", "content": "🧪 推送测试"},
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": "如果你看到这条消息，说明飞书推送已接通 ✅"},
                {"tag": "hr"},
                {"tag": "note", "elements": [
                    {"tag": "plain_text", "content": "来自竞品情报系统的测试消息"}
                ]},
            ],
        }

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m src.feishu.pusher list-chats")
        print("  python -m src.feishu.pusher find-user <email>")
        print("  python -m src.feishu.pusher test-chat <chat_id>")
        print("  python -m src.feishu.pusher test-user <open_id>")
        print("  python -m src.feishu.pusher push-daily <chat_id> [date]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list-chats":
        chats = get_chat_list()
        if not chats:
            print("No chats found. Is the bot added to any group?")
        for c in chats:
            print(f"  {c.get('chat_id')}  {c.get('name', '')}")

    elif cmd == "find-user":
        if len(sys.argv) < 3:
            print("Usage: python -m src.feishu.pusher find-user your@email.com")
            sys.exit(1)
        email = sys.argv[2]
        result = find_user_by_email(email)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if "open_id" in result:
            print(f"\nTest push to this user:")
            print(f"  python -m src.feishu.pusher test-user {result['open_id']}")

    elif cmd == "test-chat":
        if len(sys.argv) < 3:
            print("Usage: python -m src.feishu.pusher test-chat <chat_id>")
            sys.exit(1)
        chat_id = sys.argv[2]
        result = push_card(_test_card(), chat_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "test-user":
        if len(sys.argv) < 3:
            print("Usage: python -m src.feishu.pusher test-user <open_id>")
            sys.exit(1)
        open_id = sys.argv[2]
        result = push_to_user(_test_card(), open_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "push-daily":
        if len(sys.argv) < 3:
            print("Usage: python -m src.feishu.pusher push-daily <chat_id> [date]")
            sys.exit(1)
        chat_id = sys.argv[2]
        date = sys.argv[3] if len(sys.argv) >= 4 else None
        result = push_daily_card(chat_id, date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
