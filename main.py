"""
AlphaClaude — AI stock trading bot powered by Claude Code.
Receives events via Feishu WebSocket long-connection.
https://github.com/44-99/AlphaClaude
"""
import json
import os
import queue
import re
import sys
import threading
import traceback
import uuid as _uuid
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import ALERT_CHAT_IDS, LOG_LEVEL, STOCK_DATA_DIR
from feishu.bot import parse_event, reply_message, send_text
from feishu.group import check_membership
from feishu.user import get_user_label
from feishu.ws import start_ws_listener
from claude import ask_claude
from logging_config import setup_logging
from scheduler import (
    run_morning_analysis,
    run_midday_update,
    run_closing_summary,
    run_memory_consolidation,
    set_subscribers,
    start_scheduler,
    create_dynamic_task,
    delete_dynamic_task,
    list_dynamic_tasks,
)
import memory

logger = setup_logging(STOCK_DATA_DIR, LOG_LEVEL)

SESSIONS_FILE = os.path.join(STOCK_DATA_DIR, "state", "sessions.json")
SUBS_FILE = os.path.join(STOCK_DATA_DIR, "state", "subscribers.json")
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
_sessions: dict[str, dict] = {}        # conv_id → {session_id, type, label}
_subscribers: list[str] = []
_session_lock = threading.RLock()
_subs_lock = threading.RLock()
_session_queues: dict[str, tuple[queue.Queue, threading.Thread]] = {}
_session_queue_lock = threading.Lock()
_skills: list[dict] = []


# === Session persistence ===

def _load_sessions() -> dict:
    if os.path.exists(SESSIONS_FILE):
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_sessions() -> None:
    os.makedirs(STOCK_DATA_DIR, exist_ok=True)
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(_sessions, f, ensure_ascii=False, indent=2)


def _get_or_create_session(conv_id: str, session_type: str, label: str = "") -> str:
    """Return existing session UUID or create a new one. Saves to disk."""
    with _session_lock:
        if conv_id in _sessions:
            return _sessions[conv_id]["session_id"]
        sid = str(_uuid.uuid4())
        _sessions[conv_id] = {
            "session_id": sid,
            "type": session_type,
            "label": label,
            "memory_injected": False,
            "created_at": datetime.now().isoformat(),
        }
        _save_sessions()
        logger.info("新建 %s session: %s → %s", session_type, conv_id, sid[:8], extra={"category": "session", "session_id": sid})
        return sid


def _reset_session(conv_id: str) -> str:
    """Delete old session, create new UUID. Used by /new /clear."""
    with _session_lock:
        old = _sessions.pop(conv_id, None)
        sid = str(_uuid.uuid4())
        _sessions[conv_id] = {
            "session_id": sid,
            "type": old["type"] if old else "dm",
            "label": old["label"] if old else "",
            "memory_injected": False,
            "created_at": datetime.now().isoformat(),
        }
        _save_sessions()
        if old:
            logger.info("重置: %s → %s", conv_id, sid[:8], extra={"category": "session", "session_id": sid})
        return sid


# === Subscriber management ===

def _load_subs() -> list[str]:
    if os.path.exists(SUBS_FILE):
        with open(SUBS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_subs() -> None:
    os.makedirs(STOCK_DATA_DIR, exist_ok=True)
    with open(SUBS_FILE, "w", encoding="utf-8") as f:
        json.dump(_subscribers, f, ensure_ascii=False, indent=2)


def _register_subscriber(chat_id: str) -> None:
    global _subscribers
    with _subs_lock:
        if chat_id not in _subscribers:
            _subscribers.append(chat_id)
            _save_subs()
            set_subscribers(list(_subscribers))


def _unregister_subscriber(chat_id: str) -> bool:
    global _subscribers
    with _subs_lock:
        if chat_id in _subscribers:
            _subscribers.remove(chat_id)
            _save_subs()
            set_subscribers(list(_subscribers))
            return True
        return False


def _is_subscribed(chat_id: str) -> bool:
    with _subs_lock:
        return chat_id in _subscribers


# === Stock data ===

STOCK_CODE_RE = re.compile(r'\b(\d{6})\b')
STOCK_NAME_PATTERNS = [
    (re.compile(r'(贵州茅台|茅台)'), '600519'),
    (re.compile(r'(宁德时代|宁德)'), '300750'),
    (re.compile(r'(比亚迪)'), '002594'),
    (re.compile(r'(五粮液)'), '000858'),
    (re.compile(r'(隆基绿能|隆基)'), '601012'),
    (re.compile(r'(招商银行|招行)'), '600036'),
    (re.compile(r'(中国平安|平安)'), '601318'),
    (re.compile(r'(东方财富|东财)'), '300059'),
    (re.compile(r'(中芯国际|中芯)'), '688981'),
    (re.compile(r'(药明康德|药明)'), '603259'),
]


def _extract_stock_codes(text: str) -> list[str]:
    codes = STOCK_CODE_RE.findall(text)
    if not codes:
        for pattern, code in STOCK_NAME_PATTERNS:
            if pattern.search(text):
                codes.append(code)
    return list(dict.fromkeys(codes))


def _fetch_stock_context(text: str) -> tuple[str, bool]:
    from stock import get_market_overview, get_stock_detail
    codes = _extract_stock_codes(text)
    parts = []
    data_ok = False
    try:
        overview = get_market_overview()
        if "error" not in overview:
            data_ok = True
            b = overview.get("breadth", {})
            parts.append(f"【大盘概况 - {overview.get('time', '')}】")
            for idx in overview.get("indices", []):
                sign = "+" if idx.get("涨跌幅", 0) > 0 else ""
                parts.append(f"  {idx['名称']}: {idx['最新价']} ({sign}{idx['涨跌幅']}%)")
            parts.append(f"  涨跌比: {b.get('up', 0)}/{b.get('down', 0)}/{b.get('flat', 0)}")
    except (OSError, ValueError, RuntimeError):
        pass
    for code in codes:
        try:
            detail = get_stock_detail(code)
            if "error" not in detail:
                data_ok = True
                parts.append(f"\n【{detail.get('名称', code)} {code}】")
                parts.append(f"  最新价: {detail.get('最新价')} | 涨跌幅: {detail.get('涨跌幅')}%")
                parts.append(f"  换手率: {detail.get('换手率')}% | 量比: {detail.get('量比')}")
                parts.append(f"  市盈率: {detail.get('市盈率')} | 市净率: {detail.get('市净率')}")
                parts.append(f"  今开/最高/最低: {detail.get('今开')}/{detail.get('最高')}/{detail.get('最低')}")
                parts.append(f"  成交量: {detail.get('成交量')}手 | 成交额: {detail.get('成交额')}元")
        except (OSError, ValueError, RuntimeError):
            pass
    return "\n".join(parts), data_ok


# === SDK helpers ===

def _sdk_to_dict(event_obj) -> dict:
    d = {}
    if hasattr(event_obj, "header"):
        d["header"] = _obj_to_dict(event_obj.header)
    elif hasattr(event_obj, "event_type"):
        d["header"] = {"event_type": event_obj.event_type}
    else:
        d["header"] = {"event_type": "im.message.receive_v1"}
    if hasattr(event_obj, "event"):
        d["event"] = _obj_to_dict(event_obj.event)
    return d


def _obj_to_dict(obj) -> dict | list:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        return [_obj_to_dict(i) for i in obj]
    if hasattr(obj, "__dict__"):
        result = {}
        for k, v in obj.__dict__.items():
            if k.startswith("_"):
                continue
            result[k] = _obj_to_dict(v)
        return result
    return obj


# === Skills system ===

def _load_skills() -> list[dict]:
    skills = []
    if not os.path.isdir(SKILLS_DIR):
        return skills
    # Collect skill files: root-level .md files + subdirectory SKILL.md files
    skill_files = []
    for entry in os.listdir(SKILLS_DIR):
        epath = os.path.join(SKILLS_DIR, entry)
        if os.path.isdir(epath):
            skill_md = os.path.join(epath, "SKILL.md")
            if os.path.isfile(skill_md):
                skill_files.append(skill_md)
        elif entry.endswith(".md") and entry != "README.md":
            skill_files.append(epath)

    for fpath in skill_files:
        fname = os.path.basename(fpath)
        parent = os.path.basename(os.path.dirname(fpath))
        if fname == "SKILL.md":
            fname = f"{parent}/SKILL.md"
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    body = parts[2].strip()
                    cfg: dict = {"triggers": []}
                    current_key = ""
                    for line in frontmatter.strip().split("\n"):
                        stripped = line.strip()
                        if stripped.startswith("- "):
                            item = stripped[2:].strip().strip('"').strip("'")
                            if current_key == "triggers":
                                cfg.setdefault("triggers", []).append(item)
                            elif current_key:
                                prev = cfg.get(current_key, "")
                                cfg[current_key] = f"{prev}, {item}" if prev else item
                            continue
                        if ":" in stripped:
                            key, _, val = stripped.partition(":")
                            key = key.strip()
                            val = val.strip().strip('"').strip("'")
                            current_key = key
                            if key == "triggers":
                                if val:
                                    cfg["triggers"].append(val)
                            elif key:
                                cfg[key] = val
                    always_load = str(cfg.get("always_load", "")).lower() == "true"
                    if (cfg.get("triggers") or always_load) and body:
                        cfg["body"] = body
                        cfg["file"] = fname
                        cfg["always_load"] = always_load
                        skills.append(cfg)
                        tag = "前置" if always_load else f"触发: {cfg['triggers']}"
                        logger.info("已加载: %s %s", cfg.get('name', fname), tag, extra={"category": "skill"})
        except (OSError, ValueError) as e:
            logger.warning("加载失败 %s: %s", fname, e, extra={"category": "skill"})
    return skills


def _match_skills(text: str) -> str:
    if not _skills:
        return ""
    text_lower = text.lower()
    matched = []
    for skill in _skills:
        for trigger in skill.get("triggers", []):
            if trigger.lower() in text_lower:
                matched.append(skill)
                break
    if not matched:
        return ""
    parts = []
    for skill in matched:
        parts.append(f"[技能: {skill.get('name', skill.get('file', ''))}]\n{skill['body']}")
    return "\n\n---\n\n".join(parts)


def _get_always_load_skills() -> str:
    """Get context from skills with always_load: true."""
    if not _skills:
        return ""
    parts = []
    for skill in _skills:
        if skill.get("always_load"):
            parts.append(f"[交易纪律 — {skill.get('name', '')}]\n{skill['body']}")
    return "\n\n---\n\n".join(parts) if parts else ""


# === Welcome messages ===

_WELCOME_MSG = (
    "我是 AlphaClaude，A股分析助手。\n\n"
    "可用指令：\n"
    "  /sub 或 订阅 — 订阅每日定时推送\n"
    "  /unsub 或 退订 — 取消订阅\n"
    "  /status — 查看引擎运行状态\n"
    "  /task <描述> — 创建自定义分析任务\n"
    "  /task delete <id> — 删除任务\n"
    "  /tasks — 查看当前任务\n"
    "  /group <群ID> <提问> — 跨群查询（私聊可用）\n"
    "  /groups — 列出可用群\n"
    "  /new 或 新对话 — 重置对话上下文\n"
    "  /help — 显示本消息\n\n"
    "直接发送股票名称或代码即可开始分析。"
)

_GROUP_WELCOME_MSG = (
    "我是 AlphaClaude，A股分析助手。在群里 @我 即可提问分析股票。\n\n"
    "可用指令（@我后发送）：\n"
    "  /sub 或 订阅 — 订阅每日定时推送\n"
    "  /unsub 或 退订 — 取消订阅\n"
    "  /status — 查看引擎运行状态\n"
    "  /task <描述> — 创建自定义分析任务\n"
    "  /task delete <id> — 删除任务\n"
    "  /tasks — 查看当前任务\n"
    "  /new 或 新对话 — 重置对话上下文\n"
    "  /help — 显示本消息"
)


# === Command handling ===

_EXACT_COMMANDS = {
    "/help", "帮助", "help", "指令", "命令",
    "/sub", "订阅", "subscribe", "sub", "开启推送",
    "/unsub", "取消订阅", "unsubscribe", "unsub", "退订", "关闭推送",
    "/status", "status", "状态", "引擎状态",
    "/positions", "positions", "持仓", "仓位",
    "/stop", "stop", "停止引擎", "停止",
    "/groups", "groups",
    "/tasks", "tasks",
}

_COMMAND_KEYWORDS = [
    "清空", "重置", "新对话", "新开", "重新开始", "从头",
    "新建任务", "创建任务", "定时", "每天", "每周", "每月",
    "订阅", "取消", "退订", "推送",
    "任务列表", "删除任务",
]


def _parse_semantic_commands(text: str) -> list[dict] | None:
    """Use Claude to parse natural language into bot actions."""
    prompt = (
        "你是一个指令解析器。将用户的自然语言消息拆分为 bot 操作列表。\n\n"
        "支持的操作类型：\n"
        "- new_session: 清空对话历史，开始新对话\n"
        "- subscribe: 订阅每日定时推送\n"
        "- unsubscribe: 取消订阅\n"
        "- subscription_status: 查看订阅状态\n"
        "- create_task: 创建定时任务，args 为 {description, cron, prompt}\n"
        "  cron 为5字段标准格式（分 时 日 月 星期）。日字段和星期字段不能同时为*。例如：每天早上8点→0 8 * * *，每个工作日9点→0 9 * * 1-5\n"
        "- list_tasks: 列出当前任务\n"
        "- delete_task: 删除任务，args 为 {task_id}\n"
        "- stock_analysis: 股票分析请求，args 为 {query}\n"
        "- help: 显示帮助信息\n\n"
        "规则：\n"
        "1. 如果用户说「清空上下文后新建一个每天8点的定时任务」→ 拆成 [new_session, create_task]\n"
        "2. 如果用户说「每天早上8点分析茅台」→ 只有 [create_task]\n"
        "3. 如果用户说「帮我分析茅台」→ 只有 [stock_analysis]\n"
        "4. 纯股票分析请求不要拆出其他操作\n\n"
        f"用户消息：{text}\n\n"
        "只输出JSON数组，不要任何解释。格式："
        '[{"action": "...", "args": {...}}]'
    )
    try:
        result = ask_claude(prompt, timeout=30)
        import re as _re
        json_match = _re.search(r'\[.*]', result, _re.DOTALL)
        if not json_match:
            return None
        actions = json.loads(json_match.group())
        if not isinstance(actions, list):
            return None
        return actions
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _execute_actions(actions: list[dict], chat_id: str, chat_type: str,
                     sender_id: str) -> str:
    """Execute parsed actions. Returns summary string."""
    conv_id = f"{chat_id}_{sender_id}" if chat_type == "p2p" else chat_id
    results = []

    for act in actions:
        action = act.get("action", "")
        args = act.get("args", {})

        if action == "new_session":
            _reset_session(conv_id)
            results.append("[已重置对话]")

        elif action == "subscribe":
            _register_subscriber(chat_id)
            results.append("[已订阅推送]")

        elif action == "unsubscribe":
            if _unregister_subscriber(chat_id):
                results.append("[已取消订阅]")
            else:
                results.append("[未订阅]")

        elif action == "subscription_status":
            results.append("[已订阅]" if _is_subscribed(chat_id) else "[未订阅]")

        elif action == "create_task":
            desc = str(args.get("description", "自定义任务"))
            cron = str(args.get("cron", "0 9 * * 1-5"))
            prompt_tmpl = str(args.get("prompt", desc))
            task_id = create_dynamic_task(chat_id, cron, desc, prompt_tmpl)
            results.append(f"[已创建任务 {task_id}: {desc} ({cron})]")

        elif action == "list_tasks":
            tasks = list_dynamic_tasks(chat_id)
            if tasks:
                lines = ["任务列表："]
                for t in tasks:
                    lines.append(f"  [{t['id']}] {t['description']} ({t['cron']})")
                results.append("\n".join(lines))
            else:
                results.append("[无任务]")

        elif action == "delete_task":
            tid = args.get("task_id", "")
            if tid and delete_dynamic_task(tid):
                results.append(f"[已删除 {tid}]")
            else:
                results.append(f"[未找到任务 {tid}]")

        elif action == "help":
            results.append(_WELCOME_MSG)

        elif action == "stock_analysis":
            pass

        else:
            results.append(f"[未知操作: {action}]")

    return "\n".join(results) if results else ""


def _handle_command(chat_id: str, chat_type: str, text: str) -> str | None:
    """Handle exact-match bot commands. Returns reply string or None."""
    cmd = text.strip()
    cmd_lower = cmd.lower()

    if cmd_lower in ("/help", "帮助", "help", "指令", "命令"):
        return _WELCOME_MSG

    if cmd_lower in ("/sub", "订阅", "subscribe", "sub", "开启推送"):
        if chat_type == "p2p":
            return "私聊无需订阅，定时推送默认发送。"
        _register_subscriber(chat_id)
        return "已订阅定时推送（早盘 9:00 / 午间 12:00 / 收盘 15:30）。"

    if cmd_lower in ("/unsub", "取消订阅", "unsubscribe", "unsub", "退订", "关闭推送"):
        if chat_type == "p2p":
            return "私聊无需管理订阅。如不想收到推送，直接忽略即可。"
        if _unregister_subscriber(chat_id):
            return "已取消订阅。"
        return "当前未订阅。"

    if cmd_lower in ("/status", "status", "状态"):
        try:
            from tools.engine_status import format_status_text
            return format_status_text()
        except Exception as e:
            return f"无法获取引擎状态: {e}"

    if cmd_lower in ("/positions", "positions", "持仓", "仓位"):
        try:
            from tools.engine_status import format_positions_text
            return format_positions_text()
        except Exception as e:
            return f"无法获取持仓: {e}"

    if cmd_lower in ("/stop", "stop", "停止引擎", "停止"):
        # Only stop if in DM or explicitly confirm
        if chat_type != "p2p":
            return "请在私聊中使用 /stop 命令停止引擎。"
        try:
            from tools.engine_status import stop_engine
            return stop_engine()
        except Exception as e:
            return f"停止引擎失败: {e}"

    if cmd_lower in ("/groups", "groups"):
        groups = memory._list_group_sessions()
        if not groups:
            return "当前没有已注册的群。\n在群里 @我 发送任意消息即可注册。"
        lines = ["可用群："]
        for g in groups:
            label = g["label"] or g["short_id"]
            lines.append(f"  {g['short_id']} — {label}")
        lines.append("\n用法: /group <群ID> <提问内容>")
        return "\n".join(lines)

    if cmd_lower in ("/tasks", "tasks"):
        tasks = list_dynamic_tasks(chat_id)
        if not tasks:
            return "当前没有自定义任务。\n用 /task <描述> 创建，如：/task 每天早上8点分析茅台"
        lines = ["当前任务："]
        for t in tasks:
            lines.append(f"  [{t['id']}] {t['description']} (cron: {t['cron']})")
        lines.append("\n用 /task delete <id> 删除任务。")
        return "\n".join(lines)

    if cmd_lower.startswith("/group ") or cmd_lower.startswith("group "):
        return None  # handled by _process_message with async flow

    if cmd_lower.startswith("/task") or cmd_lower.startswith("task"):
        if cmd_lower in ("/task", "task"):
            return "用法：\n  /task <描述> — 创建定时分析任务\n  /task delete <id> — 删除任务\n  /tasks — 查看当前任务"

        if cmd_lower.startswith("/task delete ") or cmd_lower.startswith("task delete "):
            task_id = cmd_lower.split("delete", 1)[-1].strip()
            if delete_dynamic_task(task_id):
                return f"已删除任务 {task_id}。"
            return f"未找到任务 {task_id}。"

        desc = cmd_lower
        for prefix in ("/task ", "task "):
            if desc.startswith(prefix):
                desc = cmd[len(prefix):].strip()
                break
        return _create_task_from_nl(chat_id, desc)

    return None


def _create_task_from_nl(chat_id: str, description: str) -> str:
    """Use Claude to parse natural language task description into structured config."""
    parse_prompt = (
        f"将以下自然语言描述转换为一个定时任务配置。只输出JSON，不要任何解释。\n\n"
        f"描述：{description}\n\n"
        f"输出格式：{{\"cron\": \"分 时 日 月 星期\", \"description\": \"简短描述\", \"prompt_template\": \"分析指令\"}}\n\n"
        f"cron为5字段标准格式。日字段和星期字段不能同时为*（会导致每天执行两次）。例如：\n"
        f"- 每天早上8点 → \"0 8 * * *\"\n"
        f"- 每个工作日早上9点 → \"0 9 * * 1-5\"\n"
        f"- 每周五下午3点半 → \"30 15 * * 5\"\n\n"
        f"股票相关任务务必在prompt_template中指定分析的股票代码或名称。\n"
        f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    result = ""
    try:
        result = ask_claude(parse_prompt, timeout=60)
        import re as _re
        json_match = _re.search(r'\{[^{}]*"cron"[^{}]*}', result, _re.DOTALL)
        if not json_match:
            return f"无法解析任务描述。请尝试更具体的描述，如：/task 每天早上8点分析茅台\n\nClaude 返回：{result[:200]}"
        cfg = json.loads(json_match.group())
        task_id = create_dynamic_task(
            chat_id=chat_id,
            cron_expr=cfg["cron"],
            description=cfg.get("description", description),
            prompt_template=cfg.get("prompt_template", description),
        )
        return f"已创建任务 [{task_id}]\n描述：{cfg.get('description', description)}\n定时：{cfg['cron']}\n推送目标：当前对话"
    except json.JSONDecodeError:
        return f"任务配置解析失败。请重试或简化描述。\nClaude 返回：{result[:200]}"
    except (ValueError, KeyError) as e:
        return f"创建任务失败: {e}"


# === Session queue ===


def _session_worker(session_id: str, q: queue.Queue) -> None:
    """Process messages for a single session FIFO. Exits after 600s idle."""
    while True:
        try:
            task = q.get(timeout=600)
        except queue.Empty:
            with _session_queue_lock:
                if q.empty():
                    _session_queues.pop(session_id, None)
            return

        try:
            _process_one_message(task, session_id)
        except (OSError, ValueError, RuntimeError) as e:
            logger.error("会话工作线程异常: %s", e, exc_info=True,
                         extra={"category": "error", "session_id": session_id})
        finally:
            q.task_done()


def _ensure_session_worker(session_id: str) -> queue.Queue:
    """Return the queue for session_id, creating a worker thread if needed."""
    with _session_queue_lock:
        entry = _session_queues.get(session_id)
        if entry is None or not entry[1].is_alive():
            q = queue.Queue()
            t = threading.Thread(
                target=_session_worker, args=(session_id, q), daemon=True
            )
            t.start()
            _session_queues[session_id] = (q, t)
            return q
        return entry[0]


def _process_one_message(task: dict, session_id: str) -> None:
    """Build prompt and send to Claude, then reply. Extracted from handle()."""
    chat_id = task["chat_id"]
    chat_type = task["chat_type"]
    sender_id = task["sender_id"]
    stock_query = task["stock_query"]
    conv_id = task["conv_id"]
    group_context = task["group_context"]
    action_summary = task["action_summary"]
    message_id = task["message_id"]

    if chat_type == "p2p":
        user_message = stock_query
    else:
        user_label = get_user_label(sender_id) or sender_id
        user_message = f"[{user_label}]: {stock_query}"

    data_context, data_ok = _fetch_stock_context(stock_query)
    skill_context = _match_skills(stock_query)
    always_load_context = _get_always_load_skills()

    context_parts = []

    if always_load_context:
        context_parts.append(always_load_context)

    # Inject memory on new session
    needs_memory = (
        conv_id in _sessions
        and not _sessions[conv_id].get("memory_injected", True)
    )
    if needs_memory:
        mem_content, mem_path = memory._load_memory(conv_id, chat_type, sender_id)
        if not mem_content:
            name = get_user_label(sender_id) if chat_type == "p2p" else ("群聊 " + chat_id[-6:])
            mem_type = "user" if chat_type == "p2p" else "group"
            open_id = sender_id if chat_type == "p2p" else chat_id
            memory._create_memory_skeleton(open_id, name, mem_type)
            mem_content, mem_path = memory._load_memory(conv_id, chat_type, sender_id)
        if mem_content and "（待探索）" not in mem_content and "（暂无）" not in mem_content:
            context_parts.append(f"[用户记忆]\n{mem_content[:800]}")
        with _session_lock:
            if conv_id in _sessions:
                _sessions[conv_id]["memory_injected"] = True
                _save_sessions()

    if group_context:
        context_parts.append(group_context)
    if data_context:
        context_parts.append(data_context)
    if not data_ok:
        codes = _extract_stock_codes(stock_query)
        has_stock = bool(codes) or any(
            kw in stock_query for kw in (
                "大盘", "指数", "行情", "市场", "板块", "走势", "涨跌",
                "股票", "涨停", "跌停", "开盘", "收盘", "短线", "中线",
                "仓位", "买入", "卖出", "止损", "止盈", "推荐", "分析",
                "预测", "建议", "估值", "PE", "PB",
            )
        )
        if has_stock:
            context_parts.append("当前休市中，无实时行情数据。请基于训练知识给出分析，注明「基于历史数据」。")
    if skill_context:
        context_parts.append(f"[技能提示]\n{skill_context}")
    if action_summary:
        context_parts.append(f"[操作结果: {action_summary}]")

    if context_parts:
        prompt = user_message + "\n\n---\n" + "\n".join(context_parts)
    else:
        prompt = user_message

    response = ask_claude(prompt, session_id=session_id)

    reply_message(message_id, response)
    logger.info("已发送到 %s", chat_id, extra={"category": "reply", "chat_id": chat_id})


# === Crash hook ===


def _setup_crash_hook(alert_chat_ids: list[str]) -> None:
    """Install global exception hook: log + best-effort Feishu alert."""
    _prev_hook = sys.excepthook

    def _crash_handler(exc_type, exc_value, exc_tb):
        logger.critical("未捕获异常，进程即将崩溃", exc_info=(exc_type, exc_value, exc_tb),
                        extra={"category": "crash"})
        if alert_chat_ids:
            tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
            alert = f"[AlphaClaude 崩溃告警]\n{exc_type.__name__}: {exc_value}\n\n{''.join(tb_lines[-5:])[:500]}"
            for cid in alert_chat_ids:
                try:
                    send_text(cid, alert)
                except (OSError, ValueError, RuntimeError):
                    pass
        _prev_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _crash_handler


# === Message processing ===

def _process_message(event: dict) -> None:
    """Shared message handler — used by both WebSocket and Webhook."""
    chat_id = event["chat_id"]
    chat_type = event["chat_type"]
    sender_id = event["sender_id"]
    text = event["text"]
    message_id = event["message_id"]

    if not text:
        return

    logger.info("%s @ %s: %s", sender_id, chat_id, text[:100], extra={"category": "message", "chat_id": chat_id})

    cmd_lower = text.strip().lower()

    # Pre-process /group — resolve cross-group context before normal flow
    group_context = ""
    if cmd_lower.startswith("/group ") or cmd_lower.startswith("group "):
        rest = cmd_lower
        for prefix in ("/group ", "group "):
            if rest.startswith(prefix):
                rest = text.strip()[len(prefix):].strip()
                break
        parts = rest.split(None, 1)
        if len(parts) < 2:
            reply_message(message_id, "用法: /group <群ID> <提问内容>\n先用 /groups 查看可用群。")
            return
        short_id = parts[0]
        query = parts[1]
        groups = memory._list_group_sessions()
        target = next((g for g in groups if g["short_id"] == short_id), None)
        if not target:
            reply_message(message_id, f"未找到群 {short_id}。用 /groups 查看可用群。")
            return
        if not check_membership(target["conv_id"], sender_id):
            reply_message(message_id, f"你不在群 {short_id} 中，无法查询。用 /groups 查看你已加入的群。")
            return
        transcript = memory._read_group_transcript(target["conv_id"], limit=30)
        group_mem, _ = memory._load_memory(target["conv_id"], "group", "")
        label = target["label"] or short_id
        parts = []
        if group_mem:
            parts.append(f"[群聊记忆: {label}]\n{group_mem}")
        if transcript:
            parts.append(f"[{label} 最近消息]\n{transcript}")
        if parts:
            group_context = "\n\n".join(parts)
        text = query

    # Pre-process /new and /clear — consolidate memory before resetting
    if cmd_lower in ("/new", "/clear", "new", "clear", "/reset", "reset"):
        conv_id = f"{chat_id}_{sender_id}" if chat_type == "p2p" else chat_id
        reply_message(message_id, "正在整理记忆...")
        def _do_reset_with_consolidation():
            summary = ""
            try:
                _, summary = memory._consolidate_session(conv_id)
            except (OSError, ValueError, RuntimeError) as e:
                logger.warning("/new 前整理失败: %s", e, extra={"category": "consolidate"})
            _reset_session(conv_id)
            if summary:
                reply_message(message_id, f"[记忆已整理] {summary}\n对话已重置，新会话已就绪。")
            else:
                reply_message(message_id, "[记忆已整理，对话已重置]")
        threading.Thread(target=_do_reset_with_consolidation, daemon=True).start()
        return

    # Step 1: Exact-match commands (fast path, no Claude)
    if text.strip().lower() in _EXACT_COMMANDS:
        cmd_reply = _handle_command(chat_id, chat_type, text)
        if cmd_reply is not None:
            threading.Thread(target=reply_message, args=(message_id, cmd_reply), daemon=True).start()
            return

    # Conversation key
    conv_id = f"{chat_id}_{sender_id}" if chat_type == "p2p" else chat_id

    # Step 2: Semantic command parsing
    has_keywords = any(kw in text for kw in _COMMAND_KEYWORDS)
    stock_query: str = text
    action_summary = ""

    if has_keywords:
        actions = _parse_semantic_commands(text)
        if actions:
            cmd_actions = [a for a in actions if a.get("action") != "stock_analysis"]
            analysis_actions = [a for a in actions if a.get("action") == "stock_analysis"]

            if cmd_actions:
                action_summary = _execute_actions(cmd_actions, chat_id, chat_type, sender_id)
                if any(a.get("action") == "new_session" for a in cmd_actions):
                    conv_id = f"{chat_id}_{sender_id}" if chat_type == "p2p" else chat_id

            if analysis_actions:
                query_arg = analysis_actions[0].get("args", {}).get("query")
                stock_query = str(query_arg) if query_arg else text

            if not analysis_actions:
                msg = action_summary or "指令已执行。"
                threading.Thread(target=reply_message, args=(message_id, msg), daemon=True).start()
                return

            if action_summary:
                threading.Thread(
                    target=reply_message,
                    args=(message_id, action_summary),
                    daemon=True,
                ).start()

    # Step 3: Get or create session
    if chat_type == "p2p":
        session_type = "dm"
        label = ""
    else:
        session_type = "group"
        label = ""
    is_new = conv_id not in _sessions
    session_id = _get_or_create_session(conv_id, session_type, label)

    # Step 4: Send welcome on first interaction
    if is_new:
        welcome = _WELCOME_MSG if chat_type == "p2p" else _GROUP_WELCOME_MSG
        threading.Thread(
            target=reply_message,
            args=(message_id, welcome),
            daemon=True,
        ).start()

    # Step 5: Enqueue message to per-session queue (FIFO, no rejection)
    task = {
        "message_id": message_id,
        "chat_id": chat_id,
        "chat_type": chat_type,
        "sender_id": sender_id,
        "stock_query": stock_query,
        "conv_id": conv_id,
        "group_context": group_context,
        "action_summary": action_summary,
    }
    q = _ensure_session_worker(session_id)
    depth = q.qsize()
    if depth > 0:
        threading.Thread(
            target=reply_message,
            args=(message_id, f"排队中（前面还有{depth}个请求），请稍候..."),
            daemon=True,
        ).start()
    q.put(task)


# === WebSocket event handler ===

def _handle_sdk_event(event_obj) -> None:
    try:
        if not isinstance(event_obj, dict):
            event_dict = _sdk_to_dict(event_obj)
        else:
            event_dict = event_obj
        try:
            dump = json.dumps(event_dict, ensure_ascii=False, default=str)
            logger.debug("WS事件: %s", dump[:500], extra={"category": "ws"})
        except (TypeError, ValueError):
            pass
        event = parse_event(event_dict)
        if event is None:
            logger.info("未识别事件类型，跳过", extra={"category": "ws"})
            return
        if event["type"] == "challenge":
            return
        if event["type"] == "member_added":
            chat_id = event["chat_id"]
            send_text(chat_id, _GROUP_WELCOME_MSG)
            logger.info("已发送欢迎消息到 %s", chat_id, extra={"category": "member_added", "chat_id": chat_id})
            return
        _process_message(event)
    except (OSError, ValueError, RuntimeError) as e:
        logger.error("事件处理异常: %s", e, exc_info=True, extra={"category": "ws"})


# === FastAPI App ===

_ws_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _sessions, _subscribers, _ws_thread, _skills
    _sessions = _load_sessions()
    _subscribers = _load_subs()
    memory.set_session_state(_sessions, _session_lock)
    set_subscribers(list(_subscribers))
    _skills = _load_skills()
    _setup_crash_hook(ALERT_CHAT_IDS)
    start_scheduler()
    _ws_thread = start_ws_listener(_handle_sdk_event)
    logger.info("飞书股票机器人已启动 (WebSocket长连接)", extra={"category": "startup"})
    yield
    logger.info("飞书股票机器人已停止", extra={"category": "shutdown"})


app = FastAPI(title="StockTrading Bot", lifespan=lifespan)


@app.get("/health")
async def health():
    ws_alive = _ws_thread is not None and _ws_thread.is_alive()
    ws_status = "running" if ws_alive else "stopped"
    return {"status": "ok", "ws": ws_status, "time": datetime.now().isoformat()}


@app.post("/trigger/now")
async def trigger_now(session: str = "morning"):
    tasks = {
        "morning": run_morning_analysis,
        "midday": run_midday_update,
        "closing": run_closing_summary,
        "dream": run_memory_consolidation,
    }
    task = tasks.get(session)
    if not task:
        return {"error": f"Unknown session: {session}. Use: morning/midday/closing/dream"}
    threading.Thread(target=task, daemon=True).start()
    return {"status": "triggered", "session": session}


@app.get("/subscribers")
async def get_subscribers():
    return {"subscribers": _subscribers}


@app.get("/sessions")
async def get_sessions():
    return {"sessions": _sessions}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8800, log_level="info")
