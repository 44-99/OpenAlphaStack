"""
Feishu Bot — send messages, handle events.
"""
import json
import httpx
from feishu.auth import get_tenant_token
from config import FEISHU_API_BASE


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


def parse_event(raw: dict) -> dict | None:
    """Parse a Feishu webhook event. Returns normalized event dict or None if irrelevant."""
    # URL verification challenge
    if raw.get("type") == "url_verification":
        return {"type": "challenge", "challenge": raw.get("challenge", "")}

    header = raw.get("header", {})
    event_type = header.get("event_type", "")
    if event_type != "im.message.receive_v1":
        return None

    event = raw.get("event", {})
    message = event.get("message", {})
    sender = event.get("sender", {})
    sender_id = (sender.get("sender_id") or {}).get("open_id", "")

    # Ignore bot's own messages
    if sender_id == "":
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

    # Check @mentions for group chats
    mentions = content.get("mentions", [])
    is_mentioned = any(m.get("name", "").lower() in ["stock bot", "stock-bot"] for m in mentions)

    # For group chats, only respond when @mentioned
    # For DMs, always respond
    if chat_type == "group" and not is_mentioned and text:
        return None

    # Remove @bot from text
    for m in mentions:
        text = text.replace(f"@{m.get('name', '')}", "").strip()
    text = text.replace("@stock bot", "").replace("@stock-bot", "").strip()

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
