from __future__ import annotations

import json
import shutil
import sys
import uuid

import pytest
import tomllib

import openalphastack
from openalphastack import paths
from openalphastack.app import cli as app_cli
from openalphastack.engine import cli as engine_cli


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
    assert openalphastack.__version__
    assert (paths.PROJECT_ROOT / "pyproject.toml").exists()
    assert (paths.SRC_DIR / "openalphastack" / "app" / "main.py").exists()
    assert (paths.SRC_DIR / "openalphastack" / "engine" / "paper.py").exists()


def test_app_cli_runs_package_app(monkeypatch):
    calls = []

    class FakeUvicorn:
        @staticmethod
        def run(app, host: str, port: int, log_level: str, timeout_graceful_shutdown: int):
            calls.append((app.title, host, port, log_level, timeout_graceful_shutdown))

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)

    app_cli.main(["app", "start"])

    assert calls == [("OpenAlphaStack", "127.0.0.1", 8800, "info", 3)]


def test_pyproject_exposes_only_unified_console_script():
    data = tomllib.loads((paths.PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["scripts"] == {
        "openalphastack": "openalphastack.app.cli:main",
    }


def test_pyproject_keeps_heavy_integrations_optional():
    data = tomllib.loads((paths.PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    extras = data["project"]["optional-dependencies"]

    assert not any(item.startswith(("fastapi", "uvicorn", "akshare", "pyarrow", "lark-oapi")) for item in dependencies)
    assert {"market", "engine", "dashboard", "feishu", "all"} <= set(extras)


def test_legacy_root_launchers_are_removed():
    dockerfile = (paths.PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert not (paths.PROJECT_ROOT / "start_bot.bat").exists()
    assert 'CMD ["openalphastack", "app", "start"]' in dockerfile
    assert 'CMD ["python", "-u", "main.py"]' not in dockerfile


def test_unified_engine_list_routes_to_engine_cli(monkeypatch):
    calls = []

    monkeypatch.setattr(app_cli, "_run_engine", lambda args, **_kwargs: calls.append(args))

    app_cli.main(["engine", "list", "--mode", "paper"])

    assert calls == [["--list-runs", "--mode", "paper"]]


def test_unified_engine_status_routes_to_engine_cli(monkeypatch):
    calls = []

    monkeypatch.setattr(app_cli, "_run_engine", lambda args, **_kwargs: calls.append(args))

    app_cli.main(["engine", "status", "paper_test_run"])

    assert calls == [["--status-run", "paper_test_run"]]


def test_unified_engine_start_routes_raw_engine_args(monkeypatch):
    calls = []

    monkeypatch.setattr(app_cli, "_run_engine", lambda args, **_kwargs: calls.append(args))

    app_cli.main(["engine", "start", "--mode", "paper", "--daemon"])

    assert calls == [["--mode", "paper", "--daemon"]]


def test_unified_engine_start_help_hides_internal_control_flags(capsys):
    app_cli.main(["engine", "start", "--help"])

    out = capsys.readouterr().out
    assert "usage: openalphastack engine start" in out
    assert "--list-runs" not in out
    assert "--status-run" not in out
    assert "--stop-run" not in out
    assert "--resume-run" not in out


def test_unified_engine_resume_routes_paper_run_to_daemon_resume(monkeypatch):
    calls = []

    monkeypatch.setattr(app_cli, "_run_engine", lambda args, **_kwargs: calls.append(args))

    app_cli.main(["engine", "resume", "paper_test_run", "--daemon"])

    assert calls == [["--resume-run", "paper_test_run", "--daemon"]]


def test_engine_cli_rejects_live_mode(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["openalphastack engine", "--mode", "live"])

    with pytest.raises(SystemExit) as exc:
        engine_cli.main()

    assert exc.value.code == 2
    assert "invalid choice: 'live'" in capsys.readouterr().err


def test_unified_tools_routes_to_tool_module(monkeypatch):
    calls = []

    monkeypatch.setattr(app_cli, "_run_tool", lambda tool, args: calls.append((tool, args)))

    app_cli.main(["tools", "quote", "600519"])

    assert calls == [("quote", ["600519"])]


def test_engine_cli_builds_package_engine(monkeypatch):
    created = []
    runs = []

    class FakePaperEngine:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def run_backtest(self):
            runs.append(("backtest", None))

        def run_paper(self):
            runs.append(("paper", None))

    monkeypatch.setattr(engine_cli, "PaperEngine", FakePaperEngine)
    monkeypatch.setattr(engine_cli, "fallback_universe", lambda: ["600036"])
    monkeypatch.setattr(sys, "argv", ["openalphastack engine", "--mode", "backtest", "--start", "2025-01-01", "--end", "2025-01-31"])

    engine_cli.main()

    assert created
    assert created[0]["mode"] == "backtest"
    assert created[0]["universe"] == ["600036"]
    assert runs == [("backtest", None)]


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
        "openalphastack engine",
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
    assert cmd[:4] == [sys.executable, "-u", "-m", "openalphastack.engine.cli"]
    assert "--daemon" not in cmd
    assert kwargs["stdin"] == engine_cli.subprocess.DEVNULL
    assert kwargs["close_fds"] is True


def test_start_daemon_reuses_alive_paper_run(monkeypatch, workspace_tmp):
    existing = engine_cli.run_registry.RunRecord(
        run_id="paper_alive",
        mode="paper",
        run_dir=str(workspace_tmp / "output" / "paper_alive"),
        state_path=str(workspace_tmp / "output" / "paper_alive" / "state.json"),
        process_id=22222,
        status="observation",
        is_alive=True,
        started_at="2026-05-25T09:00:00",
        stopped_at="",
        resume_count=2,
        observation_mode=True,
        engine_meta={"status": "observation"},
    )
    popen_called = []

    monkeypatch.setattr(engine_cli.run_registry, "find_active_run", lambda mode, run_id=None: existing)
    monkeypatch.setattr(engine_cli.subprocess, "Popen", lambda *args, **kwargs: popen_called.append((args, kwargs)))
    monkeypatch.setattr(engine_cli, "_logs_dir", lambda: workspace_tmp / "logs")

    args = engine_cli.argparse.Namespace(
        mode="paper",
        capital=100000,
        start=None,
        end=None,
        universe="",
        watchlist="",
        resume=None,
        bar_period=60,
    )

    out = engine_cli.start_daemon(args)

    assert out["pid"] == 22222
    assert out["run_id"] == "paper_alive"
    assert out["existing"] is True
    assert popen_called == []


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
    monkeypatch.setattr(sys, "argv", ["openalphastack engine", "--stop-running"])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert stopped == [43210]
    assert out["stopped"][0]["run_id"] == "paper_test_run"
    assert out["stopped"][0]["mode"] == "paper"


def test_engine_cli_status_run_outputs_json(monkeypatch, capsys):
    class FakeRun:
        def to_dict(self):
            return {"run_id": "paper_test_run", "mode": "paper", "status": "running"}

    monkeypatch.setattr(engine_cli.run_registry, "get_run", lambda run_id: FakeRun())
    monkeypatch.setattr(sys, "argv", ["openalphastack engine", "--status-run", "paper_test_run"])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert out == {"run_id": "paper_test_run", "mode": "paper", "status": "running"}


def test_engine_cli_list_runs_outputs_json(monkeypatch, capsys):
    class FakeRun:
        def __init__(self, run_id: str):
            self.run_id = run_id

        def to_dict(self):
            return {"run_id": self.run_id}

    monkeypatch.setattr(engine_cli.run_registry, "list_runs", lambda mode=None: [FakeRun("paper_a"), FakeRun("live_b")])
    monkeypatch.setattr(sys, "argv", ["openalphastack engine", "--list-runs"])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert out == {"runs": [{"run_id": "paper_a"}, {"run_id": "live_b"}]}


def test_engine_cli_stop_run_outputs_json(monkeypatch, capsys):
    class FakeStop:
        def to_dict(self):
            return {"run_id": "paper_test_run", "signalled": True, "already_stopped": False}

    monkeypatch.setattr(engine_cli.run_registry, "stop_run", lambda run_id: FakeStop())
    monkeypatch.setattr(sys, "argv", ["openalphastack engine", "--stop-run", "paper_test_run"])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert out["run_id"] == "paper_test_run"
    assert out["signalled"] is True


def test_engine_cli_resume_run_requires_daemon(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["openalphastack engine", "--resume-run", "paper_test_run"])

    with pytest.raises(SystemExit) as exc:
        engine_cli.main()

    assert exc.value.code == 2
    assert "--resume-run requires --daemon" in capsys.readouterr().err


def test_engine_cli_resume_run_starts_detached_process(monkeypatch, capsys):
    plan = engine_cli.run_registry.ResumePlan(
        run_id="paper_test_run",
        mode="paper",
        args=[],
        safe_status="running",
        resume_count=3,
    )
    marked = []

    monkeypatch.setattr(engine_cli.run_registry, "build_resume_plan", lambda run_id: plan)
    monkeypatch.setattr(engine_cli.run_registry, "mark_resume_started", lambda resume_plan, pid: marked.append((resume_plan, pid)))
    monkeypatch.setattr(engine_cli, "start_daemon", lambda args: {"pid": 23456, "run_id": args.resume})
    monkeypatch.setattr(sys, "argv", ["openalphastack engine", "--resume-run", "paper_test_run", "--daemon"])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert out["pid"] == 23456
    assert out["run_id"] == "paper_test_run"
    assert out["resume"]["safe_status"] == "running"
    assert marked == [(plan, 23456)]


def test_resume_run_daemon_reuses_alive_paper_run(monkeypatch, workspace_tmp):
    existing = engine_cli.run_registry.RunRecord(
        run_id="paper_alive",
        mode="paper",
        run_dir=str(workspace_tmp / "output" / "paper_alive"),
        state_path=str(workspace_tmp / "output" / "paper_alive" / "state.json"),
        process_id=33333,
        status="observation",
        is_alive=True,
        started_at="2026-05-25T09:00:00",
        stopped_at="",
        resume_count=4,
        observation_mode=True,
        engine_meta={"status": "observation"},
    )
    monkeypatch.setattr(engine_cli.run_registry, "find_active_run", lambda mode, run_id=None: existing)
    monkeypatch.setattr(engine_cli, "_logs_dir", lambda: workspace_tmp / "logs")

    out = engine_cli.resume_run_daemon("paper_alive")

    assert out["pid"] == 33333
    assert out["run_id"] == "paper_alive"
    assert out["existing"] is True
    assert out["resume"]["safe_status"] == "observation"
