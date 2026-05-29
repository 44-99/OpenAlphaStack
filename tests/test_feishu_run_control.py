from __future__ import annotations

from alphaclaude.app import main as app_main
from alphaclaude.tools import engine_status


def test_status_run_command_formats_one_run(monkeypatch):
    calls: list[str] = []

    class FakeRun:
        run_id = "paper_test_run"
        is_alive = True
        mode = "paper"
        status = "running"
        process_id = 1234
        started_at = "2026-05-16T09:00:00"
        stopped_at = ""
        resume_count = 0

    monkeypatch.setattr(app_main.run_registry, "get_run", lambda run_id: calls.append(run_id) or FakeRun())

    reply = app_main._handle_command("chat-1", "p2p", "/status paper_test_run")

    assert calls == ["paper_test_run"]
    assert "paper_test_run" in reply
    assert "状态: running" in reply


def test_stop_run_command_requires_private_chat():
    reply = app_main._handle_command("chat-1", "group", "/stop paper_test_run")

    assert reply == "请在私聊中使用 停止 <run_id> 停止指定引擎。"


def test_stop_run_command_stops_exact_run(monkeypatch):
    calls: list[str] = []
    result = app_main.run_registry.StopResult(
        run_id="paper_test_run",
        mode="paper",
        pid=4321,
        signalled=True,
        already_stopped=False,
        status="stopped",
    )

    monkeypatch.setattr(app_main.run_registry, "stop_run", lambda run_id: calls.append(run_id) or result)

    reply = app_main._handle_command("chat-1", "p2p", "/stop paper_test_run")

    assert calls == ["paper_test_run"]
    assert "paper_test_run" in reply
    assert "已发送停止信号" in reply


def test_resume_run_command_requires_private_chat():
    reply = app_main._handle_command("chat-1", "group", "/resume paper_test_run")

    assert reply == "请在私聊中使用 恢复 <run_id> 恢复指定引擎。"


def test_resume_run_command_uses_safe_daemon_launcher(monkeypatch):
    calls: list[str] = []

    def fake_resume(run_id: str):
        calls.append(run_id)
        return {
            "pid": 5678,
            "run_id": "live_test_run",
            "resume": {"safe_status": "observation"},
        }

    monkeypatch.setattr(app_main.engine_cli, "resume_run_daemon", fake_resume)

    reply = app_main._handle_command("chat-1", "p2p", "/resume live_test_run")

    assert calls == ["live_test_run"]
    assert "live_test_run" in reply
    assert "PID: 5678" in reply
    assert "观察模式" in reply


def test_run_control_prefix_dispatches_without_claude(monkeypatch):
    started: list[tuple[object, tuple, bool]] = []

    class FakeThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.args, bool(self.daemon)))

    monkeypatch.setattr(app_main.threading, "Thread", FakeThread)

    app_main._process_message(
        {
            "chat_id": "chat-1",
            "chat_type": "p2p",
            "sender_id": "user-1",
            "text": "/status paper_test_run",
            "message_id": "message-1",
        }
    )

    assert len(started) == 1
    target, args, daemon = started[0]
    assert target is app_main._reply_exact_command
    assert args == ("chat-1", "p2p", "/status paper_test_run", "message-1", "user-1")
    assert daemon is True


def test_chinese_menu_commands_route_to_engine_status(monkeypatch):
    replies = {
        "status": "状态摘要",
        "positions": "持仓明细",
        "trades": "交易流水",
        "plan": "计划摘要",
    }
    monkeypatch.setattr(engine_status, "format_status_text", lambda: replies["status"])
    monkeypatch.setattr(engine_status, "format_positions_text", lambda: replies["positions"])
    monkeypatch.setattr(engine_status, "format_trades_text", lambda: replies["trades"])
    monkeypatch.setattr(engine_status, "format_plan_text", lambda: replies["plan"])

    assert app_main._handle_command("chat-1", "p2p", "状态") == "状态摘要"
    assert app_main._handle_command("chat-1", "p2p", "持仓") == "持仓明细"
    assert app_main._handle_command("chat-1", "p2p", "交易") == "交易流水"
    assert app_main._handle_command("chat-1", "p2p", "计划") == "计划摘要"


def test_help_advertises_chinese_menu_not_english_slash():
    reply = app_main._handle_command("chat-1", "p2p", "帮助")

    assert "机器人菜单建议配置这些中文指令" in reply
    assert "状态 —" in reply
    assert "/status" not in reply


def test_status_summary_does_not_include_holding_lines():
    runs = [{
        "run_id": "paper_2026-05-18T09-00-00",
        "mode": "paper",
        "phase": "trading",
        "is_alive": True,
        "data_time": "2026-05-18 10:00:00",
        "engine_meta": {},
        "total_value": 100100.0,
        "total_pnl": 100.0,
        "total_pnl_pct": 0.1,
        "cash": 80000.0,
        "day_pnl": 50.0,
        "day_pnl_pct": 0.05,
        "max_drawdown": 0.2,
        "holdings": {"600519": {"shares": 100, "avg_cost": 100.0, "current_price": 101.0}},
        "trade_count": 1,
        "today_trades": 1,
        "win_rate": 100.0,
        "total_commission": 5.0,
        "total_stamp_duty": 0.0,
        "market_bias": "neutral",
        "bias_confidence": 60,
        "candidates_count": 0,
        "pending_orders_count": 0,
        "rules": {"max_single_position_pct": 25.0, "max_total_position_pct": 80.0},
        "cooldown_count": 0,
        "cooldown_codes": [],
        "stopped_out_count": 0,
        "stopped_out_codes": [],
    }]

    reply = engine_status.format_status_text(runs)

    assert "持仓 1 只" in reply
    assert "600519 100股" not in reply
    assert "持仓明细" in reply
