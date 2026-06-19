"""Audit event protocol for autonomous Agent subtasks."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

AGENT_EVENTS_FILE = "events.jsonl"
TERMINAL_STATUSES = {"success", "error", "skipped"}
VALID_STATUSES = {"running", *TERMINAL_STATUSES}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_event_id() -> str:
    return f"agent_evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def record_agent_event(
    run_dir: str | Path,
    *,
    task_id: str,
    status: str,
    parent_task_id: str = "",
    role: str = "",
    summary: str = "",
    input_ref: str = "",
    output_ref: str = "",
    result_ref: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Append one Agent subtask audit event under an agent run directory."""
    run_path = Path(run_dir)
    run_path.mkdir(parents=True, exist_ok=True)
    safe_status = status if status in VALID_STATUSES else "error"
    now = _now_iso()
    event = {
        "event_id": _new_event_id(),
        "task_id": task_id,
        "parent_task_id": parent_task_id,
        "role": role,
        "status": safe_status,
        "started_at": now if safe_status == "running" else "",
        "ended_at": now if safe_status in TERMINAL_STATUSES else "",
        "summary": summary,
        "input_ref": input_ref,
        "output_ref": output_ref,
        "result_ref": result_ref,
        "error": error,
    }
    with (run_path / AGENT_EVENTS_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_agent_events(run_dir: str | Path, limit: int = 1000) -> list[dict[str, Any]]:
    """Read Agent subtask events, preserving diagnostics for corrupt rows."""
    path = Path(run_dir) / AGENT_EVENTS_FILE
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            event = {
                "event_id": f"agent_corrupt_{line_no}",
                "task_id": "events.jsonl",
                "parent_task_id": "",
                "role": "audit",
                "status": "error",
                "started_at": "",
                "ended_at": _now_iso(),
                "summary": f"events.jsonl 第 {line_no} 行损坏: {exc}",
                "input_ref": "",
                "output_ref": "",
                "result_ref": "",
                "error": str(exc),
            }
        rows.append(event)
    return rows[-limit:]


def validate_agent_events(run_dir: str | Path) -> dict[str, Any]:
    """Validate event completeness and referenced artifacts for one Agent run."""
    run_path = Path(run_dir)
    warnings: list[str] = []
    events_path = run_path / AGENT_EVENTS_FILE
    if not events_path.exists():
        warnings.append("events.jsonl missing")
        return {"ok": False, "warnings": warnings, "events": [], "tasks": {}}

    events = read_agent_events(run_path)
    tasks: dict[str, dict[str, Any]] = {}
    terminal_by_task: set[str] = set()
    running_by_task: set[str] = set()

    for event in events:
        task_id = str(event.get("task_id") or "").strip()
        if not task_id:
            warnings.append(f"event {event.get('event_id', '')} missing task_id")
            continue
        status = str(event.get("status") or "")
        task = tasks.setdefault(task_id, {
            "task_id": task_id,
            "parent_task_id": event.get("parent_task_id", ""),
            "role": event.get("role", ""),
            "status": status,
            "summary": event.get("summary", ""),
            "input_ref": event.get("input_ref", ""),
            "output_ref": event.get("output_ref", ""),
            "result_ref": event.get("result_ref", ""),
            "events": [],
        })
        task["events"].append(event)
        for key in ("parent_task_id", "role", "summary", "input_ref", "output_ref", "result_ref"):
            if event.get(key):
                task[key] = event.get(key)
        if status:
            task["status"] = status
        if status == "running":
            running_by_task.add(task_id)
        if status in TERMINAL_STATUSES:
            terminal_by_task.add(task_id)
        for ref_key in ("input_ref", "output_ref", "result_ref"):
            ref = str(event.get(ref_key) or "").strip()
            if ref:
                _validate_artifact_ref(run_path, ref, warnings)

    for task_id in sorted(running_by_task - terminal_by_task):
        warnings.append(f"task {task_id} has no terminal event")

    return {
        "ok": not warnings,
        "warnings": warnings,
        "events": events,
        "tasks": tasks,
    }


def _validate_artifact_ref(run_path: Path, ref: str, warnings: list[str]) -> None:
    if ref in {"", ".", ".."} or Path(ref).is_absolute() or ".." in Path(ref).parts:
        warnings.append(f"unsafe artifact ref: {ref}")
        return
    target = (run_path / ref).resolve()
    root = run_path.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        warnings.append(f"unsafe artifact ref: {ref}")
        return
    if not target.exists():
        warnings.append(f"missing artifact: {ref}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record autonomous Agent subtask audit events.")
    parser.add_argument("action", choices=["start", "finish"])
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--parent-task-id", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--input-ref", default="")
    parser.add_argument("--output-ref", default="")
    parser.add_argument("--result-ref", default="")
    parser.add_argument("--error", default="")
    args = parser.parse_args(argv)

    status = args.status or ("running" if args.action == "start" else "success")
    event = record_agent_event(
        args.run_dir,
        task_id=args.task_id,
        parent_task_id=args.parent_task_id,
        role=args.role,
        status=status,
        summary=args.summary,
        input_ref=args.input_ref,
        output_ref=args.output_ref,
        result_ref=args.result_ref,
        error=args.error,
    )
    print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
