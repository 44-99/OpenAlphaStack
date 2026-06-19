from __future__ import annotations

import json
import shutil
import uuid

import pytest

from alphaclaude import paths
from alphaclaude.engine import run_registry


@pytest.fixture
def workspace_tmp():
    root = paths.PROJECT_ROOT / "data" / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"run_registry_{uuid.uuid4().hex}"
    path.mkdir(exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _write_run(root, run_id: str, meta: dict | None = None, state: dict | None = None):
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    payload = state or {
        "cash": 100000,
        "holdings": {},
        "engine_meta": {
            "run_id": run_id,
            "mode": run_id.split("_", 1)[0],
            "process_id": 1234,
            "status": "running",
            "started_at": "2026-05-16T09:00:00",
        },
    }
    if meta:
        payload.setdefault("engine_meta", {}).update(meta)
    (run_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


def test_list_runs_normalizes_valid_modes(workspace_tmp, monkeypatch):
    output = workspace_tmp / "output"
    _write_run(output, "paper_2026-05-16T09-00-00")
    _write_run(output, "backtest_2026-05-16T10-00-00", meta={"process_id": 0, "status": "stopped"})
    _write_run(output, "live_2026-05-16T11-00-00", meta={"process_id": 9999, "status": "observation"})
    _write_run(output, "note_2026-05-16T12-00-00")

    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda pid: int(pid or 0) in {1234, 9999})

    runs = run_registry.list_runs()

    assert [r.run_id for r in runs] == [
        "live_2026-05-16T11-00-00",
        "backtest_2026-05-16T10-00-00",
        "paper_2026-05-16T09-00-00",
    ]
    assert runs[0].mode == "live"
    assert runs[0].is_alive is True
    assert runs[0].status == "observation"
    assert runs[1].status == "stopped"


def test_get_run_returns_exact_run(workspace_tmp, monkeypatch):
    output = workspace_tmp / "output"
    _write_run(output, "paper_2026-05-16T09-00-00", meta={"process_id": 0, "status": "stopped"})

    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda pid: False)

    record = run_registry.get_run("paper_2026-05-16T09-00-00")

    assert record.run_id == "paper_2026-05-16T09-00-00"
    assert record.mode == "paper"
    assert record.status == "stopped"


def test_get_run_treats_reused_non_engine_pid_as_stopped(workspace_tmp, monkeypatch):
    output = workspace_tmp / "output"
    _write_run(output, "paper_2026-05-16T09-00-00", meta={"process_id": 23116, "status": "running"})

    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda pid: int(pid or 0) == 23116)
    monkeypatch.setattr(run_registry, "_pid_command_line", lambda _pid: "node.exe codegraph serve --mcp")

    record = run_registry.get_run("paper_2026-05-16T09-00-00")

    assert record.is_alive is False
    assert record.status == "stopped"


def test_get_run_rejects_unknown_run(workspace_tmp, monkeypatch):
    monkeypatch.setattr(run_registry, "_output_base", lambda: workspace_tmp / "output")

    with pytest.raises(run_registry.RunNotFound) as exc:
        run_registry.get_run("paper_missing")

    assert exc.value.run_id == "paper_missing"


def test_stop_run_signals_only_requested_pid(workspace_tmp, monkeypatch):
    output = workspace_tmp / "output"
    _write_run(output, "paper_a", meta={"process_id": 1111})
    _write_run(output, "paper_b", meta={"process_id": 2222})
    stopped: list[int] = []

    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda pid: int(pid or 0) in {1111, 2222})
    monkeypatch.setattr(run_registry, "_stop_pid", lambda pid: stopped.append(pid) or True)

    result = run_registry.stop_run("paper_b")

    assert stopped == [2222]
    assert result.run_id == "paper_b"
    assert result.signalled is True
    assert result.already_stopped is False
    state = json.loads((output / "paper_b" / "state.json").read_text(encoding="utf-8"))
    assert state["engine_meta"]["status"] == "stopped"
    assert state["engine_meta"]["stopped_at"]


def test_stop_run_is_idempotent_when_pid_dead(workspace_tmp, monkeypatch):
    output = workspace_tmp / "output"
    _write_run(output, "paper_dead", meta={"process_id": 3333})
    stopped: list[int] = []

    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(run_registry, "_stop_pid", lambda pid: stopped.append(pid) or True)

    result = run_registry.stop_run("paper_dead")

    assert stopped == []
    assert result.already_stopped is True
    assert result.signalled is False


def test_build_resume_plan_is_conservative_for_live(workspace_tmp, monkeypatch):
    output = workspace_tmp / "output"
    _write_run(output, "live_2026-05-16T09-00-00", meta={"process_id": 0, "status": "stopped", "resume_count": 2})

    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda pid: False)

    plan = run_registry.build_resume_plan("live_2026-05-16T09-00-00")

    assert plan.run_id == "live_2026-05-16T09-00-00"
    assert plan.mode == "live"
    assert plan.safe_status == "observation"
    assert "--resume" in plan.args
    assert "live_2026-05-16T09-00-00" in plan.args
    assert "--daemon" not in plan.args
