"""
Feishu Bot — send messages, handle events.
"""
import json
import httpx
from feishu.auth import get_tenant_token
from config import FEISHU_API_BASE, FEISHU_BOT_NAME, FEISHU_BOT_OPEN_ID


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {get_tenant_token()}",
        "Content-Type": "application/json",
    }


def send_text(chat_id: str, text: str, root_message_id: str = None) -> dict:
    """Send a text message to a chat (group or DM)."""
    content = json.dumps({"text": text})
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": content,
    }
    if root_message_id:
        body["root_id"] = root_message_id

    resp = httpx.post(
        f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
        headers=_headers(),
        json=body,
        timeout=15,
    )
    return resp.json()


def send_post(chat_id: str, title: str, paragraphs: list, root_message_id: str = None) -> dict:
    """
    Send a rich post message.
    paragraphs is a list of lists of Feishu post elements.
    Each element: {"tag": "text", "text": "..."} or {"tag": "a", "text": "...", "href": "..."}
    """
    content = json.dumps({"zh_cn": {"title": title, "content": paragraphs}})
    body = {
        "receive_id": chat_id,
        "msg_type": "post",
        "content": content,
    }
    if root_message_id:
        body["root_id"] = root_message_id

    resp = httpx.post(
        f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
        headers=_headers(),
        json=body,
        timeout=15,
    )
    return resp.json()


def reply_message(message_id: str, text: str) -> dict:
    """Reply to a specific message in thread."""
    content = json.dumps({"text": text})
    resp = httpx.post(
        f"{FEISHU_API_BASE}/im/v1/messages/{message_id}/reply",
        headers=_headers(),
        json={"content": content, "msg_type": "text"},
        timeout=15,
    )
    return resp.json()


def update_message(message_id: str, text: str) -> dict:
    """Replace the content of an existing text message. Used for streaming replies.

    Feishu API: PATCH /im/v1/messages/{message_id}
    Replaces full content — caller must send accumulated text each time.
    """
    content = json.dumps({"text": text})
    resp = httpx.patch(
        f"{FEISHU_API_BASE}/im/v1/messages/{message_id}",
        headers=_headers(),
        json={"content": content, "msg_type": "text"},
        timeout=10,
    )
    return resp.json()


def send_card(chat_id: str, card_json: dict, root_message_id: str = None) -> dict:
    """Send a Feishu interactive card message to a chat.

    card_json is the Card Message template dict (not serialized).
    Feishu API: POST /im/v1/messages with msg_type=interactive.
    """
    content = json.dumps(card_json)
    body = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": content,
    }
    if root_message_id:
        body["root_id"] = root_message_id

    resp = httpx.post(
        f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
        headers=_headers(),
        json=body,
        timeout=15,
    )
    return resp.json()


BOT_NAMES = [n.strip() for n in FEISHU_BOT_NAME.split(",") if n.strip()] + ["stock bot", "stock-bot"]


def parse_event(raw: dict) -> dict | None:
    """Parse a Feishu event (webhook or WebSocket). Returns normalized event dict or None if irrelevant."""
    # URL verification challenge (webhook only)
    if raw.get("type") == "url_verification":
        return {"type": "challenge", "challenge": raw.get("challenge", "")}

    # WebSocket v2 wraps event inside "data"
    if raw.get("type") == "event" and "data" in raw:
        inner = raw["data"]
        header = inner.get("header", {})
        event = inner.get("event", {})
    else:
        # Webhook or WebSocket v1 format
        header = raw.get("header", {})
        event = raw.get("event", {})

    event_type = header.get("event_type", "")

    # Member added to chat (bot added to group, or new user joins)
    if event_type == "im.chat.member.user.added_v1":
        chat_id = event.get("chat_id", "")
        if chat_id:
            return {"type": "member_added", "chat_id": chat_id}
        return None

    if event_type != "im.message.receive_v1":
        return None

    message = event.get("message", {})
    sender = event.get("sender", {})
    sender_id = (sender.get("sender_id") or {}).get("open_id", "")

    # Ignore bot's own messages
    if sender_id == FEISHU_BOT_OPEN_ID:
        return None

    chat_id = message.get("chat_id", "")
    chat_type = message.get("chat_type", "")  # "group" or "p2p"
    message_id = message.get("message_id", "")
    root_id = message.get("root_id", "")
    parent_id = message.get("parent_id", "")

    # Parse content
    content_str = message.get("content", "{}")
    try:
        content = json.loads(content_str)
    except json.JSONDecodeError:
        content = {}

    text = content.get("text", "").strip()
    mentions = content.get("mentions", [])

    # Check if bot was @mentioned (by open_id or by name)
    is_mentioned = False
    for m in mentions:
        # open_id may be nested inside "id" dict (SDK/WS v2 format) or at top level (webhook)
        oid = m.get("open_id") or m.get("id", {}).get("open_id", "")
        if oid == FEISHU_BOT_OPEN_ID:
            is_mentioned = True
            break
    if not is_mentioned:
        is_mentioned = any(m.get("name", "").strip().lower() in [n.lower() for n in BOT_NAMES] for m in mentions)

    # For group chats, only respond when @mentioned
    # For DMs, always respond
    if chat_type == "group" and not is_mentioned and text:
        return None

    # Remove @bot from text
    for m in mentions:
        name = m.get("name", "")
        key = m.get("key", "")
        for placeholder in [f"@{name}", f"@{key}"]:
            text = text.replace(placeholder, "").strip()
    for bot_name in BOT_NAMES:
        text = text.replace(f"@{bot_name}", "").strip()

    return {
        "type": "message",
        "chat_id": chat_id,
        "chat_type": chat_type,
        "sender_id": sender_id,
        "message_id": message_id,
        "root_id": root_id,
        "parent_id": parent_id,
        "text": text,
    }
