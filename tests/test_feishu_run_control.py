from __future__ import annotations

from alphaclaude.app import main as app_main


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

    assert reply == "请在私聊中使用 /stop <run_id> 停止指定引擎。"


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

    assert reply == "请在私聊中使用 /resume <run_id> 恢复指定引擎。"


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
    assert args == ("chat-1", "p2p", "/status paper_test_run", "message-1")
    assert daemon is True
