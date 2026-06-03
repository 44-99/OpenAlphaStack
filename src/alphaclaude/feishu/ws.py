"""
Feishu WebSocket long-connection client using official lark-oapi SDK.
The SDK handles protobuf decoding automatically.
"""
import os
import time
import threading
from datetime import datetime
from lark_oapi.ws import Client as LarkWSClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandlerBuilder
from lark_oapi.core.enum import LogLevel
from alphaclaude.config import FEISHU_APP_ID, FEISHU_APP_SECRET, STOCK_DATA_DIR

_event_handler = None
_debug_log = os.path.join(STOCK_DATA_DIR, "logs", "ws_debug.log")


def _log(msg: str):
    """Append a timestamped line to the debug log."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_debug_log, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def listen(event_handler, reconnect_delay: int = 3):
    """
    Connect to Feishu WebSocket via official SDK.
    Runs in a daemon thread since SDK's Client.start() is blocking.
    Auto-reconnects on disconnect.
    """
    import asyncio
    import lark_oapi.ws.client as _ws_client_module

    # The lark-oapi SDK captures asyncio.get_event_loop() at module import
    # time — which happens on the main thread and returns uvicorn's already-
    # running loop.  Calling loop.run_until_complete() on a running loop
    # raises "This event loop is already running".  Replace the captured
    # loop with a fresh one bound to this daemon thread.
    _dedicated_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_dedicated_loop)
    _ws_client_module.loop = _dedicated_loop

    global _event_handler
    _event_handler = event_handler

    def on_event(event_obj):
        """Called by SDK when an im.message.receive_v1 event arrives."""
        _log(f"EVENT_RECEIVED type={type(event_obj).__name__}")
        try:
            # Try to dump raw event for debugging
            if hasattr(event_obj, "header"):
                _log(f"  header.event_type={event_obj.header.event_type}")
            elif isinstance(event_obj, dict):
                header = event_obj.get("header", {})
                _log(f"  dict header={header}")
            _event_handler(event_obj)
        except (OSError, ValueError, RuntimeError) as e:
            _log(f"EVENT_ERROR: {e}")
            print(f"[WS] 事件处理异常: {e}")
            import traceback
            traceback.print_exc()

    builder = EventDispatcherHandlerBuilder("", "")
    builder.register_p2_im_message_receive_v1(on_event)
    dispatcher = builder.build()

    while True:
        try:
            _log("CONNECTING...")
            print("[WS] 连接飞书长连接...", flush=True)
            client = LarkWSClient(
                app_id=FEISHU_APP_ID,
                app_secret=FEISHU_APP_SECRET,
                log_level=LogLevel.ERROR,
                event_handler=dispatcher,
                auto_reconnect=True,
            )
            _log("CONNECTED")
            print("[WS] 已连接", flush=True)
            client.start()
            _log("DISCONNECTED: client.start returned")
            print(f"[WS] 连接结束，{reconnect_delay}秒后重连...", flush=True)
            time.sleep(reconnect_delay)
        except Exception as e:
            _log(f"DISCONNECTED: {e}")
            print(f"[WS] 连接断开: {e}，{reconnect_delay}秒后重连...", flush=True)
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
