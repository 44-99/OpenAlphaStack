"""
Feishu WebSocket long-connection client using official lark-oapi SDK.
The SDK handles protobuf decoding automatically.
"""
import threading
from lark_oapi.ws import Client as LarkWSClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandlerBuilder
from lark_oapi.core.enum import LogLevel
from config import FEISHU_APP_ID, FEISHU_APP_SECRET

_event_handler = None


def listen(event_handler, reconnect_delay: int = 3):
    """
    Connect to Feishu WebSocket via official SDK.
    Runs in a daemon thread since SDK's Client.start() is blocking.
    Auto-reconnects on disconnect.
    """
    global _event_handler
    _event_handler = event_handler

    def on_event(event_obj):
        """Called by SDK when an im.message.receive_v1 event arrives."""
        try:
            _event_handler(event_obj)
        except Exception as e:
            print(f"[WS] 事件处理异常: {e}")
            import traceback
            traceback.print_exc()

    builder = EventDispatcherHandlerBuilder("", "")
    builder.register_p2_im_message_receive_v1(on_event)
    dispatcher = builder.build()

    while True:
        try:
            print(f"[WS] 连接飞书长连接...", flush=True)
            client = LarkWSClient(
                app_id=FEISHU_APP_ID,
                app_secret=FEISHU_APP_SECRET,
                log_level=LogLevel.ERROR,
                event_handler=dispatcher,
                auto_reconnect=False,  # we handle reconnection ourselves
            )
            print(f"[WS] 已连接", flush=True)
            client.start()
        except Exception as e:
            print(f"[WS] 连接断开: {e}，{reconnect_delay}秒后重连...", flush=True)
            import time
            time.sleep(reconnect_delay)


def start_ws_listener(event_handler):
    """Start WebSocket listener in a daemon thread."""
    t = threading.Thread(
        target=listen,
        args=(event_handler,),
        daemon=True,
        name="feishu-ws",
    )
    t.start()
    return t
