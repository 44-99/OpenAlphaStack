"""
Feishu authentication — tenant access token management.
"""
import time
import httpx
from openalphastack.config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_API_BASE

_token_cache = {"token": "", "expires_at": 0}


def get_tenant_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["token"]

    resp = httpx.post(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书认证失败: {data}")

    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expires_at"] = now + data.get("expire", 7200)
    return _token_cache["token"]
