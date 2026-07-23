"""
Feishu group chat operations — membership check.
"""
import httpx
from openalphastack.feishu.auth import get_tenant_token
from openalphastack.config import FEISHU_API_BASE


def check_membership(chat_id: str, open_id: str) -> bool:
    """Return True if open_id is a member of the given group chat."""
    try:
        resp = httpx.get(
            f"{FEISHU_API_BASE}/im/v1/chats/{chat_id}/members",
            params={"member_id_type": "open_id", "page_size": 100},
            headers={"Authorization": f"Bearer {get_tenant_token()}"},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            print(f"[群成员] API 错误: {data}", flush=True)
            return False
        items = data.get("data", {}).get("items", [])
        for member in items:
            if member.get("member_id") == open_id:
                return True
        return False
    except Exception as e:
        print(f"[群成员] 检查失败: {e}", flush=True)
        return False
