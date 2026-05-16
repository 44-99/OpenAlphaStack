# Run Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exact `run_id` controls for querying, stopping, and resuming paper, backtest, and reserved live runs from CLI and Feishu.

**Architecture:** Add a focused `alphaclaude.engine.run_registry` module that normalizes run metadata from `data/output/<run_id>/state.json`, checks PID liveness, stops only the requested PID, and builds safe resume plans. Keep process spawning in `alphaclaude.engine.cli`, and keep Feishu command handling in `alphaclaude.app.main` as a thin adapter over CLI-equivalent functions.

**Tech Stack:** Python 3.10+, argparse, dataclasses, pathlib, pytest, existing `alphaclaude.paths`, existing `alphaclaude.tools.engine_status._is_pid_alive`.

---

## File Structure

- Create: `src/alphaclaude/engine/run_registry.py`
  - Owns run discovery, metadata normalization, single-run stop logic, and resume planning.
- Modify: `src/alphaclaude/engine/cli.py`
  - Adds `--list-runs`, `--status-run`, `--stop-run`, and `--resume-run`.
  - Reuses `start_daemon()` to launch resume plans without adding another process launcher.
- Modify: `src/alphaclaude/app/main.py`
  - Extends exact command handling for `/status <run_id>`, `/stop <run_id>`, and `/resume <run_id>`.
  - Keeps `/stop` without a run ID as a guarded bulk stop for private chat only.
- Modify: `src/alphaclaude/tools/engine_status.py`
  - Adds formatting helper for one normalized run record or reuses existing formatter with a single-record list.
- Create: `tests/engine/test_run_registry.py`
  - Unit tests for registry discovery, exact lookup, stop idempotency, and resume planning.
- Modify: `tests/test_package_entrypoints.py`
  - CLI coverage for the new flags.
- Create: `tests/test_feishu_run_control.py`
  - Feishu command parser coverage for run-specific status, stop, and resume.
- Modify: `README.md`, `docs/roadmap.md`, `docs/architecture.md`
  - Update only after tests pass so documentation reflects actual behavior.

---

### Task 1: Add Run Registry Model And Listing

**Files:**
- Create: `src/alphaclaude/engine/run_registry.py`
- Test: `tests/engine/test_run_registry.py`

- [ ] **Step 1: Write failing tests for run listing and exact lookup**

```python
from __future__ import annotations

import json

import pytest

from alphaclaude.engine import run_registry


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


def test_list_runs_normalizes_valid_modes(tmp_path, monkeypatch):
    output = tmp_path / "output"
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
    assert runs[0].status == "running"
    assert runs[1].status == "stopped"


def test_get_run_returns_exact_run(tmp_path, monkeypatch):
    output = tmp_path / "output"
    _write_run(output, "paper_2026-05-16T09-00-00", meta={"process_id": 0, "status": "stopped"})

    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda pid: False)

    record = run_registry.get_run("paper_2026-05-16T09-00-00")

    assert record.run_id == "paper_2026-05-16T09-00-00"
    assert record.mode == "paper"
    assert record.status == "stopped"


def test_get_run_rejects_unknown_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_registry, "_output_base", lambda: tmp_path / "output")

    with pytest.raises(run_registry.RunNotFound) as exc:
        run_registry.get_run("paper_missing")

    assert exc.value.run_id == "paper_missing"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests\engine\test_run_registry.py -q
```

Expected: failure because `alphaclaude.engine.run_registry` does not exist.

- [ ] **Step 3: Implement registry model and listing**

Add `src/alphaclaude/engine/run_registry.py`:

```python
"""Run registry for exact run_id based engine control."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from alphaclaude.paths import DATA_DIR
from alphaclaude.tools.engine_status import _is_pid_alive

VALID_MODES = {"paper", "backtest", "live"}


class RunControlError(RuntimeError):
    """Base class for run control errors."""


class RunNotFound(RunControlError):
    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"Run not found: {run_id}")


@dataclass(slots=True)
class RunRecord:
    run_id: str
    mode: str
    run_dir: str
    state_path: str
    process_id: int | None
    status: str
    is_alive: bool
    started_at: str
    stopped_at: str
    resume_count: int
    observation_mode: bool
    engine_meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _output_base() -> Path:
    return DATA_DIR / "output"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _mode_from_run_id(run_id: str) -> str:
    return run_id.split("_", 1)[0] if "_" in run_id else ""


def _pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _record_from_dir(run_dir: Path) -> RunRecord | None:
    run_id = run_dir.name
    mode = _mode_from_run_id(run_id)
    if mode not in VALID_MODES:
        return None

    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    meta = dict(state.get("engine_meta") or {})
    process_id = _pid(meta.get("process_id"))
    is_alive = _is_pid_alive(process_id)
    stored_status = str(meta.get("status") or "").strip()
    status = "running" if is_alive else (stored_status or "unknown")
    if not is_alive and status == "running":
        status = "stopped"

    return RunRecord(
        run_id=run_id,
        mode=str(meta.get("mode") or mode),
        run_dir=str(run_dir),
        state_path=str(state_path),
        process_id=process_id,
        status=status,
        is_alive=is_alive,
        started_at=str(meta.get("started_at") or ""),
        stopped_at=str(meta.get("stopped_at") or ""),
        resume_count=int(meta.get("resume_count") or 0),
        observation_mode=bool(meta.get("observation_mode") or status == "observation"),
        engine_meta=meta,
    )


def list_runs(mode: str | None = None) -> list[RunRecord]:
    output = _output_base()
    if not output.exists():
        return []
    records: list[RunRecord] = []
    for run_dir in output.iterdir():
        if not run_dir.is_dir():
            continue
        record = _record_from_dir(run_dir)
        if record is None:
            continue
        if mode and record.mode != mode:
            continue
        records.append(record)
    records.sort(key=lambda r: r.run_id, reverse=True)
    return records


def get_run(run_id: str) -> RunRecord:
    record = _record_from_dir(_output_base() / run_id)
    if record is None:
        raise RunNotFound(run_id)
    return record
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```powershell
python -m pytest tests\engine\test_run_registry.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/alphaclaude/engine/run_registry.py tests/engine/test_run_registry.py
git commit -m "feat: add run registry"
```

---

### Task 2: Add Single-Run Stop And Resume Planning

**Files:**
- Modify: `src/alphaclaude/engine/run_registry.py`
- Test: `tests/engine/test_run_registry.py`

- [ ] **Step 1: Add failing tests for stop and resume**

Append to `tests/engine/test_run_registry.py`:

```python
def test_stop_run_signals_only_requested_pid(tmp_path, monkeypatch):
    output = tmp_path / "output"
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


def test_stop_run_is_idempotent_when_pid_dead(tmp_path, monkeypatch):
    output = tmp_path / "output"
    _write_run(output, "paper_dead", meta={"process_id": 3333})
    stopped: list[int] = []

    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(run_registry, "_stop_pid", lambda pid: stopped.append(pid) or True)

    result = run_registry.stop_run("paper_dead")

    assert stopped == []
    assert result.already_stopped is True
    assert result.signalled is False


def test_build_resume_plan_is_conservative_for_live(tmp_path, monkeypatch):
    output = tmp_path / "output"
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
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests\engine\test_run_registry.py -q
```

Expected: failures for missing `stop_run`, `_stop_pid`, `StopResult`, `build_resume_plan`, and `ResumePlan`.

- [ ] **Step 3: Implement stop and resume planning**

Add to `src/alphaclaude/engine/run_registry.py`:

```python
import os
import signal
import sys


@dataclass(slots=True)
class StopResult:
    run_id: str
    mode: str
    pid: int | None
    signalled: bool
    already_stopped: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ResumePlan:
    run_id: str
    mode: str
    args: list[str]
    safe_status: str
    resume_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_engine_meta(run_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    state_path = _output_base() / run_id / "state.json"
    state = _read_json(state_path)
    meta = dict(state.get("engine_meta") or {})
    meta.update(updates)
    state["engine_meta"] = meta
    _write_json(state_path, state)
    return meta


def _stop_pid(pid: int) -> bool:
    if not _is_pid_alive(pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def stop_run(run_id: str) -> StopResult:
    record = get_run(run_id)
    already_stopped = not record.process_id or not record.is_alive
    signalled = False
    if not already_stopped and record.process_id is not None:
        signalled = _stop_pid(record.process_id)
    status = "stopped"
    _update_engine_meta(
        run_id,
        {
            "status": status,
            "stopped_at": datetime.now().isoformat(timespec="seconds"),
        },
    )
    return StopResult(
        run_id=record.run_id,
        mode=record.mode,
        pid=record.process_id,
        signalled=signalled,
        already_stopped=already_stopped,
        status=status,
    )


def build_resume_plan(run_id: str) -> ResumePlan:
    record = get_run(run_id)
    safe_status = "observation" if record.mode == "live" else "running"
    resume_count = record.resume_count + 1
    args = [
        sys.executable,
        "-u",
        "-m",
        "alphaclaude.engine.cli",
        "--mode",
        record.mode,
        "--resume",
        record.run_id,
    ]
    return ResumePlan(
        run_id=record.run_id,
        mode=record.mode,
        args=args,
        safe_status=safe_status,
        resume_count=resume_count,
    )


def mark_resume_started(plan: ResumePlan, process_id: int) -> None:
    _update_engine_meta(
        plan.run_id,
        {
            "process_id": process_id,
            "status": plan.safe_status,
            "resume_count": plan.resume_count,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "stopped_at": "",
            "observation_mode": plan.mode == "live",
            "observation_reason": "live resume is conservative until Phase 3 safety gates are complete"
            if plan.mode == "live"
            else "",
        },
    )
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```powershell
python -m pytest tests\engine\test_run_registry.py -q
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/alphaclaude/engine/run_registry.py tests/engine/test_run_registry.py
git commit -m "feat: control engine runs by run id"
```

---

### Task 3: Wire Run Control Into Engine CLI

**Files:**
- Modify: `src/alphaclaude/engine/cli.py`
- Modify: `tests/test_package_entrypoints.py`

- [ ] **Step 1: Add failing CLI tests**

Append to `tests/test_package_entrypoints.py`:

```python
def test_engine_cli_status_run_outputs_json(monkeypatch, capsys):
    class FakeRun:
        def to_dict(self):
            return {"run_id": "paper_test_run", "mode": "paper", "status": "running"}

    monkeypatch.setattr(engine_cli.run_registry, "get_run", lambda run_id: FakeRun())
    monkeypatch.setattr(sys, "argv", ["alphaclaude-engine", "--status-run", "paper_test_run"])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert out == {"run_id": "paper_test_run", "mode": "paper", "status": "running"}


def test_engine_cli_stop_run_outputs_json(monkeypatch, capsys):
    class FakeStop:
        def to_dict(self):
            return {"run_id": "paper_test_run", "signalled": True, "already_stopped": False}

    monkeypatch.setattr(engine_cli.run_registry, "stop_run", lambda run_id: FakeStop())
    monkeypatch.setattr(sys, "argv", ["alphaclaude-engine", "--stop-run", "paper_test_run"])

    engine_cli.main()

    out = json.loads(capsys.readouterr().out)
    assert out["run_id"] == "paper_test_run"
    assert out["signalled"] is True


def test_engine_cli_resume_run_requires_daemon(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["alphaclaude-engine", "--resume-run", "paper_test_run"])

    with pytest.raises(SystemExit) as exc:
        engine_cli.main()

    assert exc.value.code == 2
    assert "--resume-run requires --daemon" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests\test_package_entrypoints.py -q
```

Expected: failures because `engine_cli.run_registry` and new CLI flags are not wired.

- [ ] **Step 3: Import registry and add parser flags**

In `src/alphaclaude/engine/cli.py`, add:

```python
from alphaclaude.engine import run_registry
```

Add parser arguments near existing `--stop-running`:

```python
    parser.add_argument("--list-runs", action="store_true",
                        help="List known paper/backtest/live runs from data/output")
    parser.add_argument("--status-run",
                        help="Return one run record by run_id as JSON")
    parser.add_argument("--stop-run",
                        help="Stop one recorded engine run by run_id")
    parser.add_argument("--resume-run",
                        help="Resume one recorded engine run by run_id; requires --daemon")
```

- [ ] **Step 4: Add CLI branches before mode validation**

In `src/alphaclaude/engine/cli.py`, after `args = parser.parse_args()` and before `if args.stop_running`, add:

```python
    if args.list_runs:
        runs = [r.to_dict() for r in run_registry.list_runs(args.mode)]
        print(json.dumps({"runs": runs}, ensure_ascii=False, indent=2))
        return

    if args.status_run:
        try:
            record = run_registry.get_run(args.status_run)
        except run_registry.RunNotFound as e:
            print(json.dumps({"error": str(e), "run_id": e.run_id}, ensure_ascii=False, indent=2))
            raise SystemExit(2)
        print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
        return

    if args.stop_run:
        try:
            result = run_registry.stop_run(args.stop_run)
        except run_registry.RunNotFound as e:
            print(json.dumps({"error": str(e), "run_id": e.run_id}, ensure_ascii=False, indent=2))
            raise SystemExit(2)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    if args.resume_run:
        if not args.daemon:
            parser.error("--resume-run requires --daemon")
        try:
            plan = run_registry.build_resume_plan(args.resume_run)
        except run_registry.RunNotFound as e:
            print(json.dumps({"error": str(e), "run_id": e.run_id}, ensure_ascii=False, indent=2))
            raise SystemExit(2)
        args.mode = plan.mode
        args.resume = plan.run_id
        info = start_daemon(args)
        run_registry.mark_resume_started(plan, int(info["pid"]))
        info["resume"] = plan.to_dict()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return
```

- [ ] **Step 5: Run CLI tests and full entrypoint tests**

Run:

```powershell
python -m pytest tests\test_package_entrypoints.py -q
```

Expected: all entrypoint tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alphaclaude/engine/cli.py tests/test_package_entrypoints.py
git commit -m "feat: add run control cli"
```

---

### Task 4: Add Feishu Run-Specific Commands

**Files:**
- Modify: `src/alphaclaude/app/main.py`
- Create: `tests/test_feishu_run_control.py`

- [ ] **Step 1: Add failing tests for command handling**

Create `tests/test_feishu_run_control.py`:

```python
from __future__ import annotations

from alphaclaude.app import main as app_main


def test_status_run_command_formats_one_run(monkeypatch):
    calls: list[str] = []

    class FakeRun:
        run_id = "paper_test_run"

        def to_dict(self):
            return {"run_id": self.run_id, "mode": "paper", "status": "running"}

    monkeypatch.setattr(app_main.run_registry, "get_run", lambda run_id: calls.append(run_id) or FakeRun())
    monkeypatch.setattr(app_main, "_format_run_control_status", lambda record: f"状态: {record.run_id}")

    reply = app_main._handle_command("chat-1", "p2p", "/status paper_test_run")

    assert calls == ["paper_test_run"]
    assert reply == "状态: paper_test_run"


def test_stop_run_command_requires_private_chat():
    reply = app_main._handle_command("chat-1", "group", "/stop paper_test_run")

    assert reply == "请在私聊中使用 /stop <run_id> 停止指定引擎。"


def test_stop_run_command_stops_exact_run(monkeypatch):
    calls: list[str] = []

    class FakeStop:
        def to_dict(self):
            return {"run_id": "paper_test_run", "signalled": True, "already_stopped": False}

    monkeypatch.setattr(app_main.run_registry, "stop_run", lambda run_id: calls.append(run_id) or FakeStop())

    reply = app_main._handle_command("chat-1", "p2p", "/stop paper_test_run")

    assert calls == ["paper_test_run"]
    assert "paper_test_run" in reply
    assert "已发送停止信号" in reply


def test_resume_run_command_requires_private_chat():
    reply = app_main._handle_command("chat-1", "group", "/resume paper_test_run")

    assert reply == "请在私聊中使用 /resume <run_id> 恢复指定引擎。"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest tests\test_feishu_run_control.py -q
```

Expected: failures because `app_main.run_registry` and run-specific commands are not wired.

- [ ] **Step 3: Import run registry and widen exact command detection**

In `src/alphaclaude/app/main.py`, add near imports:

```python
from alphaclaude.engine import run_registry
```

Add `"/resume", "resume", "恢复"` to `_EXACT_COMMANDS` only if exact `/resume` without ID should produce help text. Because `_EXACT_COMMANDS` does not match prefix commands, also update `_process_message` command routing so strings starting with `/status `, `/stop `, or `/resume ` go through `_reply_exact_command` instead of Claude. The branch should be:

```python
    lowered = text.strip().lower()
    if lowered in _EXACT_COMMANDS or lowered.startswith(("/status ", "/stop ", "/resume ")):
        threading.Thread(
            target=_reply_exact_command,
            args=(chat_id, chat_type, text, message_id),
            daemon=True,
        ).start()
        return
```

- [ ] **Step 4: Add Feishu formatting helpers**

Add near `_handle_command`:

```python
def _format_run_control_status(record: run_registry.RunRecord) -> str:
    status_icon = "🟢" if record.is_alive else "⚫"
    return (
        f"{status_icon} {record.run_id}\n"
        f"模式: {record.mode}\n"
        f"状态: {record.status}\n"
        f"PID: {record.process_id or '-'}\n"
        f"启动: {record.started_at or '-'}\n"
        f"停止: {record.stopped_at or '-'}\n"
        f"恢复次数: {record.resume_count}"
    )


def _format_stop_result(result: run_registry.StopResult) -> str:
    if result.already_stopped:
        return f"⏹️ {result.run_id} 已经停止。"
    if result.signalled:
        return f"⏹️ {result.run_id} 已发送停止信号，PID: {result.pid}。"
    return f"⚠️ {result.run_id} 停止信号发送失败，PID: {result.pid or '-'}。"
```

- [ ] **Step 5: Add command branches**

In `_handle_command`, before exact `/status` branch:

```python
    if cmd_lower.startswith("/status "):
        run_id = cmd.split(None, 1)[1].strip()
        try:
            return _format_run_control_status(run_registry.get_run(run_id))
        except run_registry.RunNotFound:
            return f"未找到 run_id: {run_id}"
        except Exception as e:
            return f"无法获取引擎状态: {e}"
```

Before exact `/stop` branch:

```python
    if cmd_lower.startswith("/stop "):
        if chat_type != "p2p":
            return "请在私聊中使用 /stop <run_id> 停止指定引擎。"
        run_id = cmd.split(None, 1)[1].strip()
        try:
            return _format_stop_result(run_registry.stop_run(run_id))
        except run_registry.RunNotFound:
            return f"未找到 run_id: {run_id}"
        except Exception as e:
            return f"停止引擎失败: {e}"

    if cmd_lower.startswith("/resume "):
        if chat_type != "p2p":
            return "请在私聊中使用 /resume <run_id> 恢复指定引擎。"
        return "恢复指定 run_id 需要通过 alphaclaude-engine --resume-run <run_id> --daemon 执行；飞书直接恢复将在下一步接入安全启动器。"
```

This keeps dangerous resume from chat disabled until a safe app-side launcher is added. If the implementation chooses to allow Feishu resume in the same pass, it must call the same helper used by CLI and must not use stdout/stderr redirection that previously caused tool hangs.

- [ ] **Step 6: Run Feishu command tests**

Run:

```powershell
python -m pytest tests\test_feishu_run_control.py -q
```

Expected: all Feishu run control tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/alphaclaude/app/main.py tests/test_feishu_run_control.py
git commit -m "feat: add Feishu run control commands"
```

---

### Task 5: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update README commands**

In `README.md`, update the engine command block to include:

```bash
alphaclaude-engine --list-runs
alphaclaude-engine --status-run paper_2026-05-16T09-00-00
alphaclaude-engine --stop-run paper_2026-05-16T09-00-00
alphaclaude-engine --resume-run paper_2026-05-16T09-00-00 --daemon
```

Update robot command table with:

```markdown
| `/status <run_id>` | 查询指定引擎运行 |
| `/stop <run_id>` | 私聊中停止指定引擎 |
```

If Feishu resume is still intentionally disabled, do not document `/resume <run_id>` as available.

- [ ] **Step 2: Update roadmap status**

In `docs/roadmap.md`, change Phase 3 `运行控制面` from P0 pending to implemented baseline if all CLI and Feishu status/stop tests pass:

```markdown
| 运行控制基础 | ✅ | 支持按 run_id 查询和停止；恢复通过 CLI 安全启动，live 恢复保持观察/暂停语义 |
```

Keep BrokerAdapter, PAPER_ONLY, order idempotency, and manual confirmation as Phase 3 P0 pending.

- [ ] **Step 3: Update architecture**

In `docs/architecture.md`, add a short run lifecycle section:

```markdown
## 运行控制面

`data/output/<run_id>/state.json` 是运行控制的事实来源。`alphaclaude.engine.run_registry` 负责扫描 `paper_*`、`backtest_*`、`live_*` 目录，读取 `engine_meta`，通过 PID liveness 判断运行态，并为 CLI 和飞书命令提供统一记录。

CLI 支持 `--list-runs`、`--status-run`、`--stop-run`、`--resume-run --daemon`。飞书侧支持查询和停止指定 `run_id`；实盘 `live` 即使恢复也只能进入观察/暂停语义，不能绕过 Phase 3 安全准入。
```

- [ ] **Step 4: Run verification baseline**

Run:

```powershell
python -m pytest -q
python -m compileall -q src\alphaclaude
$env:PYTHONPATH='src'; python -m alphaclaude.engine.cli --help
$env:PYTHONPATH='src'; python -m alphaclaude.tools.quote --help
git diff --check
```

Expected:

- `pytest`: all tests pass.
- `compileall`: no output and exit code 0.
- CLI help commands exit 0.
- `git diff --check`: no whitespace errors except acceptable CRLF warnings if present.

- [ ] **Step 5: Restart app only if command code changed**

Use the already validated non-redirect pattern:

```powershell
$line = netstat -ano | Select-String ':8800' | Select-Object -First 1
if ($line) {
  $pidText = (($line.ToString() -split '\s+') | Where-Object { $_ })[-1]
  if ($pidText -match '^\d+$') {
    Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
  }
}
Start-Sleep -Seconds 1
$env:PYTHONPATH='src'
Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY,Env:GIT_HTTP_PROXY,Env:GIT_HTTPS_PROXY,Env:http_proxy,Env:https_proxy,Env:all_proxy -ErrorAction SilentlyContinue
$p = Start-Process -FilePath 'python' -ArgumentList @('-u','-m','alphaclaude.app.cli') -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 8
$health = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8800/health -TimeoutSec 5 | Select-Object -ExpandProperty Content
[pscustomobject]@{Pid=$p.Id; Health=$health} | ConvertTo-Json
```

Expected health:

```json
{"status":"ok","ws":"running"}
```

- [ ] **Step 6: Commit and push**

```bash
git add README.md docs/roadmap.md docs/architecture.md
git commit -m "docs: update run control documentation"
git push
```

---

## Self-Review

- Spec coverage: The plan covers exact run listing, status, stop, safe resume planning, CLI surface, Feishu query/stop commands, live conservative semantics, tests, docs, and restart verification.
- Scope control: Feishu direct resume is intentionally not enabled in the first chat command pass unless implemented through the same safe daemon launcher; this avoids repeating earlier hanging process problems.
- Placeholder scan: No TBD/TODO placeholders remain.
- Type consistency: `RunRecord`, `StopResult`, and `ResumePlan` are introduced before tests rely on their fields. CLI and Feishu steps refer to the same registry module.
