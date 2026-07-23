"""Workflow event store for Dashboard observability."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

WORKFLOW_EVENTS_FILE = "workflow_events.jsonl"
WORKFLOW_ARTIFACTS_DIR = "workflow_artifacts"

WORKFLOW_STAGES: dict[str, dict[str, Any]] = {
    "research": {
        "name": "Research",
        "phase": "research",
        "members": {"market_snapshot", "agent_research", "risk_validation", "plan_writer", "research"},
    },
    "execution": {
        "name": "Execution",
        "phase": "execution",
        "members": {"state_watcher", "fastlane_tick", "intraday_event_stream", "execution"},
    },
    "evaluation": {
        "name": "Evaluation",
        "phase": "evaluation",
        "members": {"daily_report", "trade_attribution", "strategy_feedback", "evaluation"},
    },
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_event_id() -> str:
    return f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def default_workflow_edges() -> list[dict[str, Any]]:
    return [
        {
            "from": "research",
            "to": "execution",
            "kind": "data",
            "label": "Published paper plan",
            "refs": ["market.snapshot", "account.state", "plan.published"],
            "required": True,
        },
        {
            "from": "execution",
            "to": "evaluation",
            "kind": "data",
            "label": "State and ledger",
            "refs": ["account.state", "account.ledger"],
            "required": False,
        },
    ]


def workflow_stage_id(node_id: str, phase: str = "") -> str:
    """Map detailed and historical event names onto the stable product stages."""
    for stage_id, stage in WORKFLOW_STAGES.items():
        if node_id in stage["members"]:
            return stage_id
    phase_aliases = {
        "premarket": "research",
        "research": "research",
        "intraday": "execution",
        "execution": "execution",
        "postclose": "evaluation",
        "evaluation": "evaluation",
    }
    return phase_aliases.get(phase, "")


class WorkflowEventStore:
    """Append-only workflow event storage under a single run output directory."""

    def __init__(self, output_dir: str | os.PathLike[str], run_id: str | None = None):
        self.output_dir = Path(output_dir)
        self.run_id = run_id or self.output_dir.name
        self.events_path = self.output_dir / WORKFLOW_EVENTS_FILE
        self.artifacts_root = self.output_dir / WORKFLOW_ARTIFACTS_DIR

    def record_node_finish(
        self,
        *,
        phase: str,
        node_id: str,
        node_name: str,
        started_at: str = "",
        summary: str = "",
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        input_payload: Any = None,
        output_payload: Any = None,
    ) -> dict[str, Any]:
        event_id = _new_event_id()
        artifact_dir = self._write_artifacts(event_id, input_payload=input_payload, output_payload=output_payload)
        return self._append_event(
            event_id=event_id,
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="success",
            started_at=started_at or _now_iso(),
            ended_at=_now_iso(),
            duration_ms=0,
            input_refs=input_refs or [],
            output_refs=output_refs or [],
            summary=summary,
            artifact_dir=artifact_dir,
        )

    def record_node_start(
        self,
        *,
        phase: str,
        node_id: str,
        node_name: str,
        summary: str = "",
        input_refs: list[str] | None = None,
        input_payload: Any = None,
    ) -> dict[str, Any]:
        """Record a live node start event so the UI can show the current step."""
        event_id = _new_event_id()
        artifact_dir = self._write_artifacts(event_id, input_payload=input_payload)
        return self._append_event(
            event_id=event_id,
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="running",
            started_at=_now_iso(),
            ended_at="",
            duration_ms=0,
            input_refs=input_refs or [],
            output_refs=[],
            summary=summary,
            artifact_dir=artifact_dir,
        )

    def record_node_error(
        self,
        *,
        phase: str,
        node_id: str,
        node_name: str,
        error: str,
        input_payload: Any = None,
    ) -> dict[str, Any]:
        event_id = _new_event_id()
        artifact_dir = self._write_artifacts(event_id, input_payload=input_payload, error_text=error)
        return self._append_event(
            event_id=event_id,
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="error",
            started_at=_now_iso(),
            ended_at=_now_iso(),
            duration_ms=0,
            summary=error[:240],
            error=error,
            artifact_dir=artifact_dir,
        )

    def record_node_warning(
        self,
        *,
        phase: str,
        node_id: str,
        node_name: str,
        summary: str,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        input_payload: Any = None,
        output_payload: Any = None,
    ) -> dict[str, Any]:
        event_id = _new_event_id()
        artifact_dir = self._write_artifacts(event_id, input_payload=input_payload, output_payload=output_payload)
        return self._append_event(
            event_id=event_id,
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="warning",
            started_at=_now_iso(),
            ended_at=_now_iso(),
            duration_ms=0,
            input_refs=input_refs or [],
            output_refs=output_refs or [],
            summary=summary,
            artifact_dir=artifact_dir,
        )

    def record_node_skip(self, *, phase: str, node_id: str, node_name: str, summary: str) -> dict[str, Any]:
        return self._append_event(
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="skipped",
            started_at=_now_iso(),
            ended_at=_now_iso(),
            duration_ms=0,
            summary=summary,
        )

    def read_events(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []

        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if _is_noisy_legacy_tick(event):
                    continue
                event.setdefault("stage_id", workflow_stage_id(str(event.get("node_id") or ""), str(event.get("phase") or "")))
                rows.append(event)
            except json.JSONDecodeError as exc:
                rows.append({
                    "event_id": f"corrupt_{line_no}",
                    "run_id": self.run_id,
                    "phase": "system",
                    "node_id": "workflow_events",
                    "node_name": "事件文件",
                    "status": "error",
                    "started_at": "",
                    "ended_at": "",
                    "duration_ms": 0,
                    "input_refs": [],
                    "output_refs": [],
                    "summary": f"workflow_events.jsonl 第 {line_no} 行损坏: {exc}",
                    "error": str(exc),
                    "artifact_dir": "",
                })
        return rows[-limit:]

    def build_graph(self) -> dict[str, Any]:
        latest_by_stage: dict[str, dict[str, Any]] = {}
        for event in self.read_events(limit=2000):
            stage_id = event.get("stage_id")
            if stage_id in WORKFLOW_STAGES:
                latest_by_stage[stage_id] = event

        nodes = []
        for stage_id, stage in WORKFLOW_STAGES.items():
            latest = latest_by_stage.get(stage_id, {})
            nodes.append({
                "id": stage_id,
                "name": stage["name"],
                "status": latest.get("status", "idle"),
                "summary": latest.get("summary", ""),
                "last_event_id": latest.get("event_id", ""),
                "phase": stage["phase"],
                "started_at": latest.get("started_at", ""),
                "ended_at": latest.get("ended_at", ""),
                "duration_ms": latest.get("duration_ms", 0),
                "input_refs": latest.get("input_refs", []),
                "output_refs": latest.get("output_refs", []),
                "artifact_dir": latest.get("artifact_dir", ""),
            })
        return {"run_id": self.run_id, "nodes": nodes, "edges": default_workflow_edges()}

    def _append_event(self, **event: Any) -> dict[str, Any]:
        row = {
            "event_id": event.pop("event_id", _new_event_id()),
            "run_id": self.run_id,
            "phase": event.pop("phase"),
            "node_id": event.pop("node_id"),
            "node_name": event.pop("node_name"),
            "status": event.pop("status"),
            "started_at": event.pop("started_at", ""),
            "ended_at": event.pop("ended_at", ""),
            "duration_ms": event.pop("duration_ms", 0),
            "input_refs": event.pop("input_refs", []),
            "output_refs": event.pop("output_refs", []),
            "summary": event.pop("summary", ""),
            "error": event.pop("error", ""),
            "artifact_dir": event.pop("artifact_dir", ""),
        }
        row["stage_id"] = workflow_stage_id(row["node_id"], row["phase"])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def _write_artifacts(
        self,
        event_id: str,
        *,
        input_payload: Any = None,
        output_payload: Any = None,
        error_text: str = "",
    ) -> str:
        artifact_dir = self.artifacts_root / event_id
        wrote_any = False
        if input_payload is not None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "input.json").write_text(
                json.dumps(input_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            wrote_any = True
        if output_payload is not None:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "output.json").write_text(
                json.dumps(output_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            wrote_any = True
        if error_text:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "error.txt").write_text(error_text, encoding="utf-8")
            wrote_any = True
        return f"{WORKFLOW_ARTIFACTS_DIR}/{event_id}" if wrote_any else ""


def _is_noisy_legacy_tick(event: dict[str, Any]) -> bool:
    """Hide old 1s polling ticks that carried no decision signal."""
    return (
        event.get("node_id") == "fastlane_tick"
        and "事件 0 条" in str(event.get("summary") or "")
    )
