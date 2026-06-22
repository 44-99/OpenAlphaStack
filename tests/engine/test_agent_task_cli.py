from __future__ import annotations

import json

import pytest

from alphaclaude.engine import cli as engine_cli


def test_cli_agent_task_dispatches_to_scheduled_runner(monkeypatch, capsys):
    calls = []

    def fake_run(task_id, *, mode="paper"):
        calls.append((task_id, mode))
        return {"ok": True, "task_id": task_id, "run_id": "agent_test"}

    monkeypatch.setattr(engine_cli.scheduled_agent_task, "run_scheduled_agent_task", fake_run)
    monkeypatch.setattr(engine_cli.sys, "argv", ["engine", "--agent-task", "postclose_review", "--mode", "paper"])

    engine_cli.main()

    assert calls == [("postclose_review", "paper")]
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "agent_test"


def test_cli_agent_task_failure_exits_nonzero(monkeypatch, capsys):
    def fake_run(task_id, *, mode="paper"):
        return {"ok": False, "task_id": task_id, "run_id": "agent_test", "error": "boom"}

    monkeypatch.setattr(engine_cli.scheduled_agent_task, "run_scheduled_agent_task", fake_run)
    monkeypatch.setattr(engine_cli.sys, "argv", ["engine", "--agent-task", "premarket_plan", "--mode", "paper"])

    with pytest.raises(SystemExit) as exc:
        engine_cli.main()

    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "boom"
