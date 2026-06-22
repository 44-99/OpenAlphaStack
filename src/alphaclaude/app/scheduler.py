"""
Scheduled tasks — daily stock analysis, dynamic task management.
"""
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from alphaclaude.config import STOCK_DATA_DIR
from alphaclaude.feishu.bot import send_text
from alphaclaude.app.stock import format_market_report
from alphaclaude.claude import ask_claude, build_trading_prompt
from alphaclaude.app.memory import _list_modified_transcripts, _uuid_to_conv, _consolidate_session
from alphaclaude.engine.workflow_events import WorkflowEventStore
from alphaclaude.tools.engine_status import _is_pid_alive

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


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


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


# === Paper engine scheduling ===

_PROJECT_ROOT = os.path.dirname(STOCK_DATA_DIR)


def _pre_market_paper() -> None:
    """盘前自动启动模拟盘引擎：检查交易日 → 停旧 → 起新"""
    _today = date.today()
    from alphaclaude.engine.trading_calendar import is_trading_day

    if not is_trading_day(_today):
        logger.info("今日非交易日，跳过模拟盘启动", extra={"category": "paper"})
        return

    # 防重复：同一天只启动一次
    _sentinel = os.path.join(STOCK_DATA_DIR, "state", ".paper_last_start")
    _today_str = _today.isoformat()
    if os.path.exists(_sentinel):
        try:
            if open(_sentinel).read().strip() == _today_str:
                logger.info("今日模拟盘已启动过，跳过", extra={"category": "paper"})
                return
        except OSError:
            pass

    logger.info("盘前模拟盘启动开始", extra={"category": "paper"})
    try:
        # Step 1: 停止所有运行中的 paper 引擎
        _r1 = subprocess.run(
            [sys.executable, "-m", "alphaclaude.engine.cli",
             "--stop-running", "--mode", "paper"],
            capture_output=True, text=True, timeout=30,
            cwd=_PROJECT_ROOT,
        )
        _stdout = _r1.stdout.strip() if _r1.stdout else ""
        logger.info("停旧模拟盘: %s", _stdout or "无运行中进程", extra={"category": "paper"})

        # Step 2: 启动新的 paper daemon
        _r2 = subprocess.run(
            [sys.executable, "-m", "alphaclaude.engine.cli",
             "--mode", "paper", "--daemon"],
            capture_output=True, text=True, timeout=30,
            cwd=_PROJECT_ROOT,
        )
        _stdout2 = _r2.stdout.strip() if _r2.stdout else ""
        logger.info("新模拟盘: %s", _stdout2, extra={"category": "paper"})

        # 标记今日已启动
        os.makedirs(os.path.dirname(_sentinel), exist_ok=True)
        with open(_sentinel, "w") as _f:
            _f.write(_today_str)

        _send_to_all(f"[模拟盘] 今日模拟盘已启动 | {_today_str}")
    except Exception as e:
        logger.error("模拟盘启动失败: %s", e, exc_info=True, extra={"category": "paper"})


def _post_market_paper() -> None:
    """盘后自动停止模拟盘引擎"""
    _today = date.today()
    if _today.weekday() >= 5:
        return

    logger.info("盘后模拟盘停止", extra={"category": "paper"})
    try:
        _r = subprocess.run(
            [sys.executable, "-m", "alphaclaude.engine.cli",
             "--stop-running", "--mode", "paper"],
            capture_output=True, text=True, timeout=30,
            cwd=_PROJECT_ROOT,
        )
        _stdout = _r.stdout.strip() if _r.stdout else ""
        logger.info("停模拟盘: %s", _stdout or "无运行中进程", extra={"category": "paper"})

        # 同时生成日报
        from alphaclaude.engine.cli import run_registry
        _runs = run_registry.list_runs("paper")
        if _runs:
            _last = _runs[0]
            _dr = subprocess.run(
                [sys.executable, "-m", "alphaclaude.tools.daily_report",
                 _last.run_id],
                capture_output=True, text=True, timeout=60,
                cwd=_PROJECT_ROOT,
            )
            if _dr.stdout.strip():
                _send_to_all(_dr.stdout.strip())
    except Exception as e:
        logger.error("模拟盘停止失败: %s", e, exc_info=True, extra={"category": "paper"})


# === External scheduled Agent tasks ===

def _agent_task_run_id(task_id: str, today: date) -> str:
    return f"agent_{today.isoformat()}_{task_id}"


def _agent_task_sentinel_path(task_id: str) -> str:
    return os.path.join(STOCK_DATA_DIR, "state", f".agent_task_{task_id}_last_start")


def _agent_task_output_dir(task_id: str, today: date) -> str:
    return os.path.join(STOCK_DATA_DIR, "output", _agent_task_run_id(task_id, today))


def _is_trading_day_for_agent_task(today: date) -> bool:
    from alphaclaude.engine.trading_calendar import is_trading_day

    return is_trading_day(today)


def _agent_task_started_today(task_id: str, today: date) -> bool:
    sentinel = _agent_task_sentinel_path(task_id)
    if not os.path.exists(sentinel):
        return False
    try:
        return open(sentinel, encoding="utf-8").read().strip() == today.isoformat()
    except OSError:
        return False


def _mark_agent_task_started(task_id: str, today: date) -> None:
    sentinel = _agent_task_sentinel_path(task_id)
    os.makedirs(os.path.dirname(sentinel), exist_ok=True)
    with open(sentinel, "w", encoding="utf-8") as f:
        f.write(today.isoformat())


def _is_agent_task_process_running(task_id: str, today: date) -> bool:
    state_path = os.path.join(_agent_task_output_dir(task_id, today), "state.json")
    if not os.path.exists(state_path):
        return False
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError, TypeError):
        return False
    meta = state.get("engine_meta") or {}
    status = str(meta.get("status") or "")
    if status in {"completed", "failed", "stopped"}:
        return False
    try:
        pid = int(meta.get("process_id") or 0)
    except (TypeError, ValueError):
        return False
    return _is_pid_alive(pid)


def _scheduled_agent_task_command(task_id: str, mode: str = "paper") -> list[str]:
    return [
        sys.executable,
        "-u",
        "-m",
        "alphaclaude.engine.cli",
        "--agent-task",
        task_id,
        "--mode",
        mode,
    ]


def _record_agent_task_launch_error(task_id: str, today: date, error: str) -> None:
    run_id = _agent_task_run_id(task_id, today)
    output_dir = _agent_task_output_dir(task_id, today)
    try:
        store = WorkflowEventStore(output_dir, run_id=run_id)
        store.record_node_error(
            phase="system",
            node_id="agent_task_launch",
            node_name="Agent 任务启动",
            error=error,
            input_payload={"task_id": task_id, "run_id": run_id},
        )
    except Exception as exc:
        logger.error("Agent 任务启动失败事件写入失败: %s", exc, extra={"category": "agent_task"})


def _record_agent_task_launch_warning(task_id: str, today: date, summary: str) -> None:
    run_id = _agent_task_run_id(task_id, today)
    output_dir = _agent_task_output_dir(task_id, today)
    try:
        store = WorkflowEventStore(output_dir, run_id=run_id)
        store.record_node_warning(
            phase="system",
            node_id="agent_task_launch",
            node_name="Agent 任务启动",
            summary=summary,
            input_payload={"task_id": task_id, "run_id": run_id},
        )
    except Exception as exc:
        logger.error("Agent 任务启动告警事件写入失败: %s", exc, extra={"category": "agent_task"})


def _launch_scheduled_agent_task(task_id: str, *, today: date | None = None, mode: str = "paper") -> dict:
    today = today or date.today()
    if not _is_trading_day_for_agent_task(today):
        logger.info("今日非交易日，跳过 Agent 任务 %s", task_id, extra={"category": "agent_task"})
        return {"started": False, "task_id": task_id, "reason": "non_trading_day"}
    if _agent_task_started_today(task_id, today):
        logger.info("今日 Agent 任务已启动过，跳过 %s", task_id, extra={"category": "agent_task"})
        return {"started": False, "task_id": task_id, "reason": "already_started"}
    if _is_agent_task_process_running(task_id, today):
        logger.warning("Agent 任务仍在运行，跳过重复启动 %s", task_id, extra={"category": "agent_task"})
        _record_agent_task_launch_warning(task_id, today, "同任务进程仍在运行，跳过重复启动")
        return {"started": False, "task_id": task_id, "reason": "already_running"}

    run_id = _agent_task_run_id(task_id, today)
    logs_dir = os.path.join(STOCK_DATA_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    out_path = os.path.join(logs_dir, f"{run_id}.out.log")
    err_path = os.path.join(logs_dir, f"{run_id}.err.log")
    env = os.environ.copy()
    src_path = os.path.join(_PROJECT_ROOT, "src")
    env["PYTHONPATH"] = src_path if not env.get("PYTHONPATH") else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )

    cmd = _scheduled_agent_task_command(task_id, mode)
    out_f = open(out_path, "ab")
    err_f = open(err_path, "ab")
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=_PROJECT_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                close_fds=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            error = str(exc)
            _record_agent_task_launch_error(task_id, today, error)
            logger.error("Agent 任务启动失败 %s: %s", task_id, error, extra={"category": "agent_task"})
            return {"started": False, "task_id": task_id, "run_id": run_id, "reason": "launch_failed", "error": error}
    finally:
        out_f.close()
        err_f.close()

    _mark_agent_task_started(task_id, today)
    logger.info("Agent 任务已启动: %s pid=%s", task_id, proc.pid, extra={"category": "agent_task"})
    return {
        "started": True,
        "task_id": task_id,
        "run_id": run_id,
        "pid": proc.pid,
        "stdout": out_path,
        "stderr": err_path,
        "cmd": cmd,
    }


def _pre_market_agent_task() -> None:
    try:
        _launch_scheduled_agent_task("premarket_plan", mode="paper")
    except Exception as e:
        logger.error("盘前 Agent 任务启动失败: %s", e, exc_info=True, extra={"category": "agent_task"})


def _post_market_agent_task() -> None:
    try:
        _launch_scheduled_agent_task("postclose_review", mode="paper")
    except Exception as e:
        logger.error("盘后 Agent 任务启动失败: %s", e, exc_info=True, extra={"category": "agent_task"})


def start_scheduler(include_market_jobs: bool = True):
    """Initialize scheduler and restore user-created tasks.

    The Feishu app no longer owns the market-analysis cycle. Paper/live engines
    generate pre-market plans and intraday/post-close reports, so app startup
    should pass include_market_jobs=False and keep only memory/dynamic tasks.
    """
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler()
    if include_market_jobs:
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
    # Agent 定时任务：调度器只启动独立 CLI 进程，不在 scheduler 线程里运行 Claude。
    _scheduler.add_job(_pre_market_agent_task, CronTrigger(hour=8, minute=30, day_of_week="mon-fri"),
                       id="agent_premarket_plan", name="Agent 盘前计划")
    _scheduler.add_job(_post_market_agent_task, CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
                       id="agent_postclose_review", name="Agent 盘后复盘")
    # 模拟盘自动调度：盘前 8:30 启动，盘后 15:05 停止（交易日历兜底非交易日跳过）
    _scheduler.add_job(_pre_market_paper, CronTrigger(hour=8, minute=30, day_of_week="mon-fri"),
                       id="paper_start", name="模拟盘启动")
    _scheduler.add_job(_post_market_paper, CronTrigger(hour=15, minute=5, day_of_week="mon-fri"),
                       id="paper_stop", name="模拟盘停止")
    _scheduler.start()
    if include_market_jobs:
        msg = "定时任务已启动: 模拟盘 8:30/15:05 + 工作日 9:00/12:00/15:30 + 记忆整理 3:17/15:17"
    else:
        msg = "定时任务已启动: 模拟盘 8:30/15:05 + 记忆整理 3:17/15:17 + 自定义任务；内置行情分析由引擎负责"
    logger.info(msg, extra={"category": "startup"})
    restore_dynamic_tasks()
