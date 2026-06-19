"""Run registry for exact run_id based engine control."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
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
    """Raised when a requested run_id is not present in data/output."""

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


def _output_base() -> Path:
    return DATA_DIR / "output"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _mode_from_run_id(run_id: str) -> str:
    return run_id.split("_", 1)[0] if "_" in run_id else ""


def _pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_command_line(pid: int) -> str:
    """Return the command line for a PID when the platform exposes it."""
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=3,
            )
            for line in (result.stdout or "").splitlines():
                if line.startswith("CommandLine="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            pass
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\").CommandLine",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=3,
            )
            return (result.stdout or "").strip()
        except Exception:
            return ""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _is_engine_pid(pid: int | None) -> bool:
    """Return true only when the stored PID still belongs to an engine process."""
    if not _is_pid_alive(pid):
        return False
    if pid is None:
        return False
    command = _pid_command_line(pid)
    if not command:
        return True
    return (
        "alphaclaude.engine.cli" in command
        or "alphaclaude engine" in command
        or "alphaclaude\\engine\\cli.py" in command
        or "alphaclaude/engine/cli.py" in command
    )


def _record_from_dir(run_dir: Path) -> RunRecord | None:
    if not run_dir.is_dir():
        return None

    run_id = run_dir.name
    mode = _mode_from_run_id(run_id)
    if mode not in VALID_MODES:
        return None

    state_path = run_dir / "state.json"
    state = _read_json(state_path)
    meta = dict(state.get("engine_meta") or {})
    process_id = _pid(meta.get("process_id"))
    stored_status = str(meta.get("status") or "").strip()
    stopped_at = str(meta.get("stopped_at") or "")
    is_alive = (
        _is_engine_pid(process_id)
        and stored_status not in {"stopped", "completed", "failed"}
        and not stopped_at
    )
    if is_alive:
        status = stored_status if stored_status in {"paused", "observation"} else "running"
    elif process_id or stored_status in {"running", "observation", "paused"}:
        status = "stopped"
    else:
        status = stored_status or "unknown"

    resume_count = 0
    try:
        resume_count = int(meta.get("resume_count") or 0)
    except (TypeError, ValueError):
        resume_count = 0

    return RunRecord(
        run_id=run_id,
        mode=str(meta.get("mode") or mode),
        run_dir=str(run_dir),
        state_path=str(state_path),
        process_id=process_id,
        status=status,
        is_alive=is_alive,
        started_at=str(meta.get("started_at") or ""),
        stopped_at=stopped_at,
        resume_count=resume_count,
        observation_mode=bool(meta.get("observation_mode") or status == "observation"),
        engine_meta=meta,
    )


def list_runs(mode: str | None = None) -> list[RunRecord]:
    output = _output_base()
    if not output.exists():
        return []

    records: list[RunRecord] = []
    for run_dir in output.iterdir():
        record = _record_from_dir(run_dir)
        if record is None:
            continue
        if mode and record.mode != mode:
            continue
        records.append(record)
    records.sort(key=lambda r: _run_sort_key(r.run_id), reverse=True)
    return records


def get_run(run_id: str) -> RunRecord:
    record = _record_from_dir(_output_base() / run_id)
    if record is None:
        raise RunNotFound(run_id)
    return record


def find_active_run(mode: str, run_id: str | None = None) -> RunRecord | None:
    """Return a live run for the given mode, optionally preferring one run_id."""
    if run_id:
        try:
            record = get_run(run_id)
        except RunNotFound:
            return None
        return record if record.mode == mode and record.is_alive else None

    records = list_runs(mode)
    for record in records:
        if record.is_alive:
            return record
    return None


def _run_sort_key(run_id: str) -> str:
    if "_" not in run_id:
        return run_id
    return run_id.split("_", 1)[1]


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
    observation_reason = ""
    if plan.mode == "live":
        observation_reason = "live resume is conservative until Phase 3 safety gates are complete"
    _update_engine_meta(
        plan.run_id,
        {
            "process_id": process_id,
            "status": plan.safe_status,
            "resume_count": plan.resume_count,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "stopped_at": "",
            "observation_mode": plan.mode == "live",
            "observation_reason": observation_reason,
        },
    )
