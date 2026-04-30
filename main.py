"""
AlphaClaude — AI stock trading bot powered by Claude Code.
Receives events via Feishu WebSocket long-connection.
https://github.com/44-99/AlphaClaude
"""
import asyncio
import json
import os
import threading
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import STOCK_DATA_DIR
from feishu.bot import parse_event, reply_message
from feishu.ws import listen as ws_listen
from claude.client import ask_claude, build_chat_prompt
from scheduler.tasks import (
    run_morning_analysis,
    run_midday_update,
    run_closing_summary,
    update_subscribers,
)

HISTORY_FILE = os.path.join(STOCK_DATA_DIR, "conversations.json")
_conversations: dict[str, list[dict]] = {}
_history_lock = threading.RLock()


def _load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_history() -> None:
    os.makedirs(STOCK_DATA_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(_conversations, f, ensure_ascii=False, indent=2)


def _get_history(conv_id: str) -> list[dict]:
    if conv_id not in _conversations:
        _conversations[conv_id] = []
    return _conversations[conv_id]


def _add_to_history(conv_id: str, role: str, content: str) -> None:
    with _history_lock:
        hist = _get_history(conv_id)
        hist.append({"role": role, "content": content, "time": datetime.now().isoformat()})
        if len(hist) > 50:
            hist[:] = hist[-50:]
        _save_history()


def _register_subscriber(chat_id: str) -> None:
    try:
        with _history_lock:
            current_subs = []
            for entry in _get_history("__subscribers__"):
                cid = entry.get("content", "")
                if cid and cid not in current_subs:
                    current_subs.append(cid)
            if chat_id not in current_subs:
                current_subs.append(chat_id)
                _conversations["__subscribers__"] = [
                    {"role": "system", "content": cid, "time": datetime.now().isoformat()}
                    for cid in current_subs
                ]
                _save_history()
                update_subscribers(current_subs)
    except Exception:
        pass


def _process_message(event: dict) -> None:
    """Shared message handler — used by both WebSocket and Webhook."""
    chat_id = event["chat_id"]
    sender_id = event["sender_id"]
    text = event["text"]
    message_id = event["message_id"]

    if not text:
        return

    _register_subscriber(chat_id)
    print(f"[消息] {sender_id} @ {chat_id}: {text[:100]}")

    def handle():
        try:
            conv_id = f"{chat_id}_{sender_id}" if event["chat_type"] == "p2p" else chat_id
            history = _get_history(conv_id)

            _add_to_history(conv_id, "user", text)
            prompt = build_chat_prompt(text, history)
            response = ask_claude(prompt)
            _add_to_history(conv_id, "assistant", response)

            reply_message(message_id, response)
            print(f"[回复] 已发送到 {chat_id}")
        except Exception as e:
            print(f"[错误] 处理消息失败: {e}")
            import traceback
            traceback.print_exc()
            try:
                reply_message(message_id, f"分析出错了: {e}")
            except Exception:
                pass

    threading.Thread(target=handle, daemon=True).start()


# === WebSocket event handler (async, runs in asyncio loop) ===
async def _ws_event_handler(data: dict) -> None:
    """Handle event from WebSocket — converts to sync processing."""
    msg_type = data.get("type", "")

    # Log all received events for debugging
    if msg_type not in ("ping", "pong", ""):
        print(f"[WS事件] type={msg_type}, keys={list(data.keys())}")
        # Dump a truncated version for debugging
        try:
            dump = json.dumps(data, ensure_ascii=False, default=str)
            print(f"[WS事件内容] {dump[:500]}")
        except Exception:
            pass

    # URL verification
    if msg_type == "url_verification":
        return

    # Parse through existing parse_event logic
    event = parse_event(data)
    if event is None:
        if msg_type not in ("ping", "pong", "", "url_verification"):
            print(f"[WS] 未识别事件类型，跳过")
        return

    if event["type"] == "challenge":
        return

    _process_message(event)


# === Scheduler Setup ===
_scheduler_started = False


def start_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_morning_analysis,
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri"),
        id="morning", name="早盘分析",
    )
    scheduler.add_job(
        run_midday_update,
        CronTrigger(hour=12, minute=0, day_of_week="mon-fri"),
        id="midday", name="午间更新",
    )
    scheduler.add_job(
        run_closing_summary,
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
        id="closing", name="收盘总结",
    )
    scheduler.start()
    print("定时任务已启动: 工作日 9:00/12:00/15:30")


# === FastAPI App ===
_ws_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _conversations, _ws_task
    _conversations = _load_history()
    start_scheduler()

    # Start WebSocket long-connection as background task
    _ws_task = asyncio.create_task(ws_listen(_ws_event_handler))
    print("飞书股票机器人已启动 (WebSocket长连接)")

    yield

    # Shutdown
    if _ws_task:
        _ws_task.cancel()
        try:
            await _ws_task
        except asyncio.CancelledError:
            pass
    print("飞书股票机器人已停止")


app = FastAPI(title="StockTrading Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    ws_status = "running" if (_ws_task and not _ws_task.done()) else "stopped"
    return {"status": "ok", "ws": ws_status, "time": datetime.now().isoformat()}


@app.post("/trigger/now")
async def trigger_now(session: str = "morning"):
    tasks = {
        "morning": run_morning_analysis,
        "midday": run_midday_update,
        "closing": run_closing_summary,
    }
    task = tasks.get(session)
    if not task:
        return {"error": f"Unknown session: {session}. Use: morning/midday/closing"}
    threading.Thread(target=task, daemon=True).start()
    return {"status": "triggered", "session": session}


@app.get("/subscribers")
async def get_subscribers():
    return {"subscribers": _get_history("__subscribers__")}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8800, log_level="info")
