"""
Feishu WebSocket long-connection client.
Connects directly to Feishu servers — no ngrok or public URL needed.
"""
import asyncio
import json
import httpx
import websockets
from config import FEISHU_APP_ID, FEISHU_APP_SECRET

FEISHU_DOMAIN = "https://open.feishu.cn"
WS_ENDPOINT = "/callback/ws/endpoint"


async def _get_ws_url() -> str:
    """Get WebSocket connection URL from Feishu."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{FEISHU_DOMAIN}{WS_ENDPOINT}",
            headers={"locale": "zh"},
            json={"AppID": FEISHU_APP_ID, "AppSecret": FEISHU_APP_SECRET},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取WebSocket连接失败: {data}")
        return data.get("data", {}).get("URL", "")


async def listen(event_handler, reconnect_delay: int = 3):
    """
    Connect to Feishu WebSocket and call event_handler(data) for each event.
    Auto-reconnects on disconnect.
    """
    while True:
        try:
            ws_url = await _get_ws_url()
            print(f"[WS] 连接飞书长连接...")
            async with websockets.connect(ws_url, ping_interval=None) as ws:
                print(f"[WS] 已连接")
                async for message in ws:
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type", "")
                    if msg_type == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                        continue

                    try:
                        await event_handler(data)
                    except Exception as e:
                        print(f"[WS] 事件处理异常: {e}")
                        import traceback
                        traceback.print_exc()

        except asyncio.CancelledError:
            print("[WS] 连接已取消")
            break
        except Exception as e:
            print(f"[WS] 连接断开: {e}，{reconnect_delay}秒后重连...")
            await asyncio.sleep(reconnect_delay)
