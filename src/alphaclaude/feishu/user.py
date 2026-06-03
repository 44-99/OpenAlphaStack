"""
Feishu user info — lookup display name by open_id, cached.
"""
import httpx
from alphaclaude.feishu.auth import get_tenant_token
from alphaclaude.config import FEISHU_API_BASE

_cache: dict[str, str] = {}


def get_user_label(sender_id: str) -> str:
    """Return '显示名 \xb7abcd' label for group chat attribution."""
    if sender_id in _cache:
        return _cache[sender_id]

    try:
        resp = httpx.get(
            f"{FEISHU_API_BASE}/contact/v3/users/{sender_id}?user_id_type=open_id",
            headers={"Authorization": f"Bearer {get_tenant_token()}"},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            user = data.get("data", {}).get("user", {})
            name = user.get("name", "") or user.get("nickname", "") or "未知用户"
        else:
            name = "未知用户"
    except Exception:
        name = "未知用户"

    suffix = sender_id[-4:] if len(sender_id) >= 4 else sender_id
    label = f"{name} \xb7{suffix}"
    _cache[sender_id] = label
    return label
