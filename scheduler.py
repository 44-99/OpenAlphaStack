"""
Scheduled tasks — daily stock analysis, dynamic task management.
"""
import json
import logging
import os
import uuid
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import STOCK_DATA_DIR
from feishu.bot import send_text
from stock import format_market_report
from claude import ask_claude, build_trading_prompt
from memory import _list_modified_transcripts, _uuid_to_conv, _consolidate_session

logger = logging.getLogger("scheduler")

TASKS_FILE = os.path.join(STOCK_DATA_DIR, "state", "tasks.json")
_scheduler: BackgroundScheduler | None = None
_subscribers: list[str] = []


# === Subscriber management ===

def set_subscribers(chat_ids: list[str]) -> None:
    global _subscribers
    _subscribers = list(set(chat_ids))


def get_subscribers() -> list[str]:
    return list(_subscribers)


# === Scheduler API ===

def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def add_task(task_id: str, cron_expr: str, func, name: str = "") -> None:
    """Register a dynamic job with cron expression."""
    if _scheduler is None:
        raise RuntimeError("Scheduler not started")
    trigger = CronTrigger.from_crontab(cron_expr)
    _scheduler.add_job(func, trigger, id=task_id, name=name, replace_existing=True)
    logger.info("已注册: %s (%s)", name or task_id, cron_expr, extra={"category": "task"})


def remove_task(task_id: str) -> bool:
    """Remove a dynamic job. Returns False if not found."""
    if _scheduler is None:
        return False
    try:
        _scheduler.remove_job(task_id)
        logger.info("已移除: %s", task_id, extra={"category": "task"})
        return True
    except (KeyError, ValueError):
        return False


def list_tasks() -> list[dict]:
    """List all scheduled jobs."""
    if _scheduler is None:
        return []
    jobs = _scheduler.get_jobs()
    return [
        {
            "id": j.id,
            "name": j.name or j.id,
            "next_run": str(j.next_run_time) if j.next_run_time else "N/A",
        }
        for j in jobs
    ]


# === Task persistence ===

def load_tasks() -> dict[str, dict]:
    """Load user-created tasks from disk. Returns {task_id: config}."""
    if not os.path.exists(TASKS_FILE):
        return {}
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tasks(tasks: dict[str, dict]) -> None:
    """Persist tasks to disk."""
    os.makedirs(STOCK_DATA_DIR, exist_ok=True)
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def _make_task_runner(chat_id: str, description: str, prompt_template: str):
    """Factory: return a callable that runs the analysis and sends to the chat."""
    def run():
        logger.info("%s -> %s", description, chat_id, extra={"category": "task_exec", "chat_id": chat_id})
        try:
            market_data = format_market_report()
            prompt = build_trading_prompt(market_data, context=prompt_template)
            analysis = ask_claude(prompt, timeout=180)
            msg = f"[自定义任务] {description}\n\n{analysis}"
            send_text(chat_id, msg)
        except (OSError, ValueError, RuntimeError) as e:
            logger.error("%s: %s", description, e, extra={"category": "task_error"})
    return run


def restore_dynamic_tasks() -> None:
    """Load persisted tasks and register them with the scheduler."""
    tasks = load_tasks()
    for task_id, cfg in tasks.items():
        runner = _make_task_runner(cfg["chat_id"], cfg["description"], cfg["prompt_template"])
        add_task(task_id, cfg["cron"], runner, cfg["description"])
    logger.info("已恢复 %d 个自定义任务", len(tasks), extra={"category": "task"})


def create_dynamic_task(chat_id: str, cron_expr: str, description: str,
                        prompt_template: str, created_by: str = "") -> str:
    """Create a new dynamic task, persist it, and register with scheduler. Returns task_id."""
    task_id = f"dyn_{uuid.uuid4().hex[:8]}"
    tasks = load_tasks()
    tasks[task_id] = {
        "chat_id": chat_id,
        "cron": cron_expr,
        "description": description,
        "prompt_template": prompt_template,
        "created_by": created_by,
        "created_at": datetime.now().isoformat(),
    }
    save_tasks(tasks)
    runner = _make_task_runner(chat_id, description, prompt_template)
    add_task(task_id, cron_expr, runner, description)
    return task_id


def delete_dynamic_task(task_id: str) -> bool:
    """Delete a user-created task. Returns False if not found."""
    tasks = load_tasks()
    if task_id not in tasks:
        return False
    del tasks[task_id]
    save_tasks(tasks)
    remove_task(task_id)
    return True


def list_dynamic_tasks(chat_id: str = None) -> list[dict]:
    """List user-created tasks, optionally filtered by chat_id."""
    tasks = load_tasks()
    result = []
    for task_id, cfg in tasks.items():
        if chat_id and cfg.get("chat_id") != chat_id:
            continue
        result.append({
            "id": task_id,
            "cron": cfg["cron"],
            "description": cfg["description"],
            "chat_id": cfg.get("chat_id", ""),
            "created_at": cfg.get("created_at", ""),
        })
    return result


# === Built-in scheduled tasks ===

def _send_to_all(message: str) -> None:
    """Send a message to all subscribers. No-op if none."""
    if not _subscribers:
        logger.info("无订阅者，跳过推送", extra={"category": "scheduled"})
        return
    for chat_id in _subscribers:
        try:
            send_text(chat_id, message)
            logger.debug("已发送到 %s", chat_id)
        except (OSError, TypeError) as e:
            logger.error("发送失败 %s: %s", chat_id, e)


def run_morning_analysis() -> None:
    logger.info("早盘分析开始", extra={"category": "morning"})
    try:
        market_data = format_market_report()
        prompt = build_trading_prompt(market_data, context="现在是早盘开盘前，请重点给出今日的操作策略和可以关注的标的。")
        analysis = ask_claude(prompt, timeout=180)
        _send_to_all(f"[早盘简报] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{analysis}")
    except (OSError, ValueError, RuntimeError) as e:
        logger.error("早盘分析失败: %s", e, exc_info=True, extra={"category": "morning"})


def run_midday_update() -> None:
    logger.info("午间更新开始", extra={"category": "midday"})
    try:
        market_data = format_market_report()
        prompt = build_trading_prompt(market_data, context="现在是午间休盘，请重点总结上午走势，给出下午的操作建议和可以关注的标的。")
        analysis = ask_claude(prompt, timeout=180)
        _send_to_all(f"[午间速报] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{analysis}")
    except (OSError, ValueError, RuntimeError) as e:
        logger.error("午间更新失败: %s", e, exc_info=True, extra={"category": "midday"})


def run_closing_summary() -> None:
    logger.info("收盘总结开始", extra={"category": "closing"})
    try:
        market_data = format_market_report()
        prompt = build_trading_prompt(market_data, context="现在是收盘后，请做全天复盘总结，给出明日展望和可以提前关注的标的。")
        analysis = ask_claude(prompt, timeout=180)
        _send_to_all(f"[收盘总结] {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{analysis}")
    except (OSError, ValueError, RuntimeError) as e:
        logger.error("收盘总结失败: %s", e, exc_info=True, extra={"category": "closing"})


def run_memory_consolidation() -> None:
    """Dreaming task: scan modified transcripts and update memory files every 12 hours."""
    logger.info("开始记忆整理", extra={"category": "dream"})
    try:
        uuids = _list_modified_transcripts(hours=12)
        if not uuids:
            logger.info("无修改过的 transcript，跳过", extra={"category": "dream"})
            return

        logger.info("发现 %d 个活跃 session", len(uuids), extra={"category": "dream"})

        updated = 0
        for sid in uuids:
            conv_id = _uuid_to_conv(sid)
            if not conv_id:
                continue
            ok, _ = _consolidate_session(conv_id)
            if ok:
                updated += 1

        logger.info("记忆整理完成，更新 %d 个", updated, extra={"category": "dream"})
    except (OSError, ValueError, RuntimeError) as e:
        logger.error("记忆整理任务失败: %s", e, exc_info=True, extra={"category": "dream"})


def start_scheduler():
    """Initialize scheduler with built-in jobs and restore user-created tasks."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(run_morning_analysis, CronTrigger(hour=9, minute=0, day_of_week="mon-fri"),
                       id="morning", name="早盘分析")
    _scheduler.add_job(run_midday_update, CronTrigger(hour=12, minute=0, day_of_week="mon-fri"),
                       id="midday", name="午间更新")
    _scheduler.add_job(run_closing_summary, CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
                       id="closing", name="收盘总结")
    _scheduler.add_job(run_memory_consolidation, CronTrigger(hour=3, minute=17),
                       id="dream_am", name="记忆整理(凌晨)")
    _scheduler.add_job(run_memory_consolidation, CronTrigger(hour=15, minute=17),
                       id="dream_pm", name="记忆整理(下午)")
    _scheduler.start()
    logger.info("定时任务已启动: 工作日 9:00/12:00/15:30 + 记忆整理 3:17/15:17", extra={"category": "startup"})
    restore_dynamic_tasks()
