"""Workflow event store for Dashboard observability."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

WORKFLOW_EVENTS_FILE = "workflow_events.jsonl"
WORKFLOW_CONFIG_FILE = "workflow_config.json"
WORKFLOW_ARTIFACTS_DIR = "workflow_artifacts"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_event_id() -> str:
    return f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def default_workflow_config() -> dict[str, Any]:
    """Return the safe built-in workflow template."""
    return {
        "version": 1,
        "nodes": {
            "market_snapshot": {"enabled": True, "locked": False, "params": {}},
            "sub_agent_a": {"enabled": True, "locked": False, "params": {}},
            "sub_agent_b": {"enabled": True, "locked": False, "params": {}},
            "sub_agent_c": {"enabled": True, "locked": False, "params": {}},
            "merge_decision": {"enabled": True, "locked": False, "params": {}},
            "bull_bear_debate": {"enabled": True, "locked": False, "params": {"max_rounds": 1}},
            "risk_validation": {
                "enabled": True,
                "locked": True,
                "params": {"max_single_position_pct": 25, "max_total_position_pct": 80},
            },
            "plan_writer": {"enabled": True, "locked": True, "params": {}},
            "state_watcher": {"enabled": True, "locked": False, "params": {}},
            "fastlane_tick": {"enabled": True, "locked": False, "params": {"tick_interval_sec": 1}},
            "signal_scan": {"enabled": True, "locked": False, "params": {}},
            "execution_check": {"enabled": True, "locked": True, "params": {}},
            "order_simulator": {"enabled": True, "locked": True, "params": {}},
            "ledger_writer": {"enabled": True, "locked": True, "params": {}},
            "alert_router": {"enabled": True, "locked": False, "params": {}},
            "daily_report": {"enabled": True, "locked": False, "params": {}},
            "ledger_pairing": {"enabled": True, "locked": False, "params": {}},
            "agent_reflection": {"enabled": True, "locked": False, "params": {}},
        },
    }


def default_workflow_edges() -> list[dict[str, str]]:
    chain = [
        "market_snapshot",
        "sub_agent_a",
        "sub_agent_b",
        "sub_agent_c",
        "merge_decision",
        "bull_bear_debate",
        "risk_validation",
        "plan_writer",
        "state_watcher",
        "fastlane_tick",
        "signal_scan",
        "execution_check",
        "order_simulator",
        "ledger_writer",
        "alert_router",
        "daily_report",
        "ledger_pairing",
        "agent_reflection",
    ]
    return [{"from": left, "to": right} for left, right in zip(chain, chain[1:])]


class WorkflowEventStore:
    """Append-only workflow event storage under a single run output directory."""

    def __init__(self, output_dir: str | os.PathLike[str], run_id: str | None = None):
        self.output_dir = Path(output_dir)
        self.run_id = run_id or self.output_dir.name
        self.events_path = self.output_dir / WORKFLOW_EVENTS_FILE
        self.config_path = self.output_dir / WORKFLOW_CONFIG_FILE
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

    def record_config_update(self, *, summary: str, config: dict[str, Any]) -> dict[str, Any]:
        event_id = _new_event_id()
        artifact_dir = self._write_artifacts(event_id, output_payload=config)
        return self._append_event(
            event_id=event_id,
            phase="system",
            node_id="workflow_config",
            node_name="流程配置",
            status="success",
            started_at=_now_iso(),
            ended_at=_now_iso(),
            duration_ms=0,
            summary=summary,
            artifact_dir=artifact_dir,
        )

    def read_events(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []

        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
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

    def read_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return self.write_config(default_workflow_config())
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default_workflow_config()

    def write_config(self, config: dict[str, Any]) -> dict[str, Any]:
        merged = default_workflow_config()
        incoming_nodes = config.get("nodes") if isinstance(config, dict) else {}
        if not isinstance(incoming_nodes, dict):
            incoming_nodes = {}

        for node_id, incoming in incoming_nodes.items():
            if node_id not in merged["nodes"] or not isinstance(incoming, dict):
                continue
            node = merged["nodes"][node_id]
            incoming_params = incoming.get("params") if isinstance(incoming.get("params"), dict) else {}
            node["params"].update(incoming_params)
            node["enabled"] = True if node.get("locked") else bool(incoming.get("enabled", node["enabled"]))

        merged["updated_at"] = _now_iso()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return merged

    def build_graph(self) -> dict[str, Any]:
        config = self.read_config()
        latest_by_node: dict[str, dict[str, Any]] = {}
        for event in self.read_events(limit=2000):
            node_id = event.get("node_id")
            if node_id:
                latest_by_node[node_id] = event

        nodes = []
        for node_id, node in config["nodes"].items():
            latest = latest_by_node.get(node_id, {})
            nodes.append({
                "id": node_id,
                "name": latest.get("node_name") or _node_display_name(node_id),
                "enabled": node.get("enabled", True),
                "locked": node.get("locked", False),
                "status": latest.get("status", "idle"),
                "summary": latest.get("summary", ""),
                "last_event_id": latest.get("event_id", ""),
                "phase": latest.get("phase", ""),
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


def _node_display_name(node_id: str) -> str:
    names = {
        "market_snapshot": "市场快照",
        "sub_agent_a": "子代理A",
        "sub_agent_b": "子代理B",
        "sub_agent_c": "子代理C",
        "merge_decision": "合并决策",
        "bull_bear_debate": "多空辩论",
        "risk_validation": "风控校验",
        "plan_writer": "计划写入",
        "state_watcher": "状态观察",
        "fastlane_tick": "盘中快车道",
        "signal_scan": "信号扫描",
        "execution_check": "执行检查",
        "order_simulator": "订单模拟",
        "ledger_writer": "账本写入",
        "alert_router": "告警路由",
        "daily_report": "盘后日报",
        "ledger_pairing": "成交配对",
        "agent_reflection": "Agent反思",
    }
    return names.get(node_id, node_id)
