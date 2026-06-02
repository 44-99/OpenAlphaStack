from __future__ import annotations

import asyncio

from alphaclaude.app import main as app_main
from alphaclaude.app import dashboard as app_dashboard


def test_arm_forced_exit_timer_starts_daemon(monkeypatch):
    calls = {}

    class FakeTimer:
        def __init__(self, timeout, target, args):
            calls["timeout"] = timeout
            calls["target"] = target
            calls["args"] = args
            self.daemon = False
            self.started = False

        def start(self):
            self.started = True
            calls["started"] = True

    monkeypatch.setattr(app_dashboard.threading, "Timer", FakeTimer)

    timer = app_dashboard._arm_forced_exit_timer(1.5)

    assert calls["timeout"] == 1.5
    assert calls["args"] == [0]
    assert calls["started"] is True
    assert timer.daemon is True
    assert timer.started is True


def test_lifespan_resets_sse_shutdown_and_arms_forced_exit(monkeypatch):
    shutdown_calls = []
    stop_calls = []
    force_calls = []

    monkeypatch.setattr(app_main, "_load_sessions", lambda: {})
    monkeypatch.setattr(app_main, "_load_subs", lambda: [])
    monkeypatch.setattr(app_main.memory, "set_session_state", lambda *_args: None)
    monkeypatch.setattr(app_main, "set_subscribers", lambda _subs: None)
    monkeypatch.setattr(app_main, "_load_skills", lambda: [])
    monkeypatch.setattr(app_main, "_setup_crash_hook", lambda _chat_ids: None)
    monkeypatch.setattr(app_main, "start_scheduler", lambda include_market_jobs=False: None)
    monkeypatch.setattr(app_main, "start_ws_listener", lambda _handler: object())
    monkeypatch.setattr(app_dashboard, "shutdown_sse", lambda: shutdown_calls.append("shutdown"))
    monkeypatch.setattr(app_dashboard, "arm_forced_exit_timer", lambda timeout_seconds=3.0: force_calls.append(timeout_seconds))
    monkeypatch.setattr("scheduler.stop_scheduler", lambda: stop_calls.append("stop"))

    app_dashboard._sse_shutdown = True

    async def _exercise():
        async with app_main.lifespan(app_main.app):
            assert app_dashboard._sse_shutdown is False

    asyncio.run(_exercise())

    assert force_calls == [3.0]
    assert shutdown_calls == ["shutdown"]
    assert stop_calls == ["stop"]
