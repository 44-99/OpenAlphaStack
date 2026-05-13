from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest

import alphaclaude
from alphaclaude import paths
from alphaclaude.app import cli as app_cli
from alphaclaude.engine import cli as engine_cli


@pytest.fixture
def workspace_tmp():
    root = paths.PROJECT_ROOT / "data" / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"entrypoints_{uuid.uuid4().hex}"
    path.mkdir(exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_package_exposes_project_paths():
    assert alphaclaude.__version__
    assert paths.PROJECT_ROOT.name == "AlphaClaude"
    assert (paths.SRC_DIR / "alphaclaude" / "app" / "main.py").exists()
    assert (paths.SRC_DIR / "alphaclaude" / "engine" / "paper.py").exists()


def test_app_cli_runs_package_app(monkeypatch):
    calls = []

    class FakeUvicorn:
        @staticmethod
        def run(app, host: str, port: int, log_level: str):
            calls.append((app.title, host, port, log_level))

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)

    app_cli.main()

    assert calls == [("StockTrading Bot", "0.0.0.0", 8800, "info")]


def test_engine_cli_builds_package_engine(monkeypatch):
    created = []
    runs = []

    class FakePaperEngine:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def run_backtest(self, claude_every: int = 1):
            runs.append(("backtest", claude_every))

        def run_paper(self):
            runs.append(("paper", None))

    monkeypatch.setattr(engine_cli, "PaperEngine", FakePaperEngine)
    monkeypatch.setattr(engine_cli, "fallback_universe", lambda: ["600036"])
    monkeypatch.setattr(sys, "argv", ["alphaclaude-engine", "--mode", "backtest", "--start", "2025-01-01", "--end", "2025-01-31"])

    engine_cli.main()

    assert created
    assert created[0]["mode"] == "backtest"
    assert created[0]["universe"] == ["600036"]
    assert runs == [("backtest", 1)]


def test_engine_cli_daemon_starts_detached_process(monkeypatch, workspace_tmp, capsys):
    popen_calls = []

    class FakeProcess:
        pid = 12345

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return FakeProcess()

    (workspace_tmp / "output").mkdir()
    (workspace_tmp / "logs").mkdir()
    monkeypatch.chdir(paths.PROJECT_ROOT)
    monkeypatch.setattr(engine_cli, "_output_base", lambda: workspace_tmp / "output")
    monkeypatch.setattr(engine_cli, "_logs_dir", lambda: workspace_tmp / "logs")
    monkeypatch.setattr(engine_cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(engine_cli.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(sys, "argv", [
        "alphaclaude-engine",
        "--mode", "paper",
        "--capital", "100000",
        "--daemon",
        "--resume", "paper_test_run",
    ])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert out["pid"] == 12345
    assert out["run_id"] == "paper_test_run"
    assert out["stdout"].endswith("paper_test_run.out.log")
    assert out["stderr"].endswith("paper_test_run.err.log")
    assert popen_calls
    cmd, kwargs = popen_calls[0]
    assert cmd[:4] == [sys.executable, "-u", "-m", "alphaclaude.engine.cli"]
    assert "--daemon" not in cmd
    assert kwargs["stdin"] == engine_cli.subprocess.DEVNULL
    assert kwargs["close_fds"] is True


def test_engine_cli_stop_running_uses_pid_metadata(monkeypatch, workspace_tmp, capsys):
    output = workspace_tmp / "output"
    run_dir = output / "paper_test_run"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({"engine_meta": {"process_id": 43210}}),
        encoding="utf-8",
    )
    stopped = []

    output.mkdir(exist_ok=True)
    monkeypatch.setattr(engine_cli, "_output_base", lambda: output)
    monkeypatch.setattr(engine_cli, "_is_pid_alive", lambda pid: int(pid) == 43210)
    monkeypatch.setattr(engine_cli, "_stop_pid", lambda pid: stopped.append(pid) or True)
    monkeypatch.setattr(sys, "argv", ["alphaclaude-engine", "--stop-running"])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert stopped == [43210]
    assert out["stopped"][0]["run_id"] == "paper_test_run"
    assert out["stopped"][0]["mode"] == "paper"
