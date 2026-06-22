"""Scheduled Agent task entrypoints for external CLI processes."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from alphaclaude.engine.agent_task_runner import AgentTaskResult, AgentTaskRunner
from alphaclaude.engine.clock import TradingClock
from alphaclaude.engine.ledger import Ledger
from alphaclaude.engine.pipeline import OvernightPipeline
from alphaclaude.engine.plan import PlanManager
from alphaclaude.engine.state import EngineState
from alphaclaude.engine.workflow_events import WorkflowEventStore
from alphaclaude.paths import DATA_DIR

VALID_SCHEDULED_AGENT_TASKS = {"premarket_plan", "postclose_review"}


class UnknownScheduledAgentTask(ValueError):
    """Raised when an unsupported scheduled Agent task is requested."""


def _output_base() -> Path:
    path = DATA_DIR / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scheduled_run_id(task_id: str, today: date | None = None) -> str:
    day = today or date.today()
    return f"agent_{day.isoformat()}_{task_id}"


def run_scheduled_agent_task(
    task_id: str,
    *,
    mode: str = "paper",
    today: date | None = None,
) -> dict[str, Any]:
    """Run one scheduled Agent task inside the current process."""
    if task_id not in VALID_SCHEDULED_AGENT_TASKS:
        raise UnknownScheduledAgentTask(f"Unknown scheduled Agent task: {task_id}")

    run_id = scheduled_run_id(task_id, today)
    output_dir = _output_base() / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    state = EngineState(str(output_dir))
    started_at = datetime.now().isoformat(timespec="seconds")
    state.set_engine_meta(
        mode="agent",
        source_mode=mode,
        run_id=run_id,
        agent_task_id=task_id,
        process_id=os.getpid(),
        status="running",
        started_at=started_at,
        stopped_at="",
        observation_mode=False,
    )
    workflow = WorkflowEventStore(output_dir, run_id=run_id)

    try:
        if task_id == "premarket_plan":
            payload = _run_premarket(output_dir, run_id, mode, state)
        else:
            payload = _run_postclose(output_dir, run_id, workflow)
    except Exception as exc:
        error = str(exc)
        state.set_engine_meta(status="failed", stopped_at=datetime.now().isoformat(timespec="seconds"), error=error)
        workflow.record_node_error(
            phase="system",
            node_id=task_id,
            node_name=_task_node_name(task_id),
            error=error,
        )
        return {
            "ok": False,
            "task_id": task_id,
            "run_id": run_id,
            "run_dir": str(output_dir),
            "error": error,
        }

    task_ok = _payload_ok(task_id, payload)
    state.set_engine_meta(
        status="completed" if task_ok else "failed",
        stopped_at=datetime.now().isoformat(timespec="seconds"),
    )
    return {
        "ok": task_ok,
        "task_id": task_id,
        "run_id": run_id,
        "run_dir": str(output_dir),
        "result": payload,
    }


def _run_premarket(output_dir: Path, run_id: str, mode: str, state: EngineState) -> dict[str, Any]:
    plan = PlanManager(str(output_dir))
    ledger = Ledger(str(output_dir))
    clock = TradingClock()
    pipeline = OvernightPipeline(state, plan, ledger, clock, str(output_dir), mode=mode)
    result = pipeline.run_full()
    plan.mark_premarket_plan_generated(clock.now())
    return result


def _run_postclose(output_dir: Path, run_id: str, workflow: WorkflowEventStore) -> dict[str, Any]:
    workflow.record_node_start(
        phase="postclose",
        node_id="trade_attribution",
        node_name="交易归因",
        summary="正在启动盘后 Agent 复盘任务",
        input_refs=["artifact.plan.json", "account.state", "account.ledger", "workflow.events"],
    )
    runner = AgentTaskRunner(output_dir, run_id=run_id)
    result = runner.run_postclose_review(review_context=_build_postclose_context(output_dir))
    status = "warning" if result.audit_warnings or not result.ok else "success"
    summary = (
        f"盘后 Agent 复盘完成，但审计告警 {len(result.audit_warnings)} 条"
        if result.audit_warnings
        else "盘后 Agent 复盘完成"
        if result.ok
        else "盘后 Agent 复盘未成功，保留 artifacts 供排查"
    )
    _record_agent_result(
        workflow,
        status=status,
        phase="postclose",
        node_id="trade_attribution",
        node_name="交易归因",
        summary=summary,
        result=result,
        output_refs=["review.review_report", "review.strategy_attribution"],
    )
    return _result_payload(result)


def _record_agent_result(
    workflow: WorkflowEventStore,
    *,
    status: str,
    phase: str,
    node_id: str,
    node_name: str,
    summary: str,
    result: AgentTaskResult,
    output_refs: list[str],
) -> None:
    payload = _result_payload(result)
    if status == "warning":
        workflow.record_node_warning(
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            summary=summary,
            output_refs=output_refs,
            output_payload=payload,
        )
    elif status == "success":
        workflow.record_node_finish(
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            summary=summary,
            output_refs=output_refs,
            output_payload=payload,
        )
    else:
        workflow.record_node_error(
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            error=summary,
            input_payload=payload,
        )


def _result_payload(result: AgentTaskResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "ok": result.ok,
        "returncode": result.returncode,
        "error": result.error,
        "artifacts_dir": str(result.artifacts_dir),
        "audit_warnings": result.audit_warnings,
        "agent_events": len(result.agent_events),
        "parsed_artifacts": sorted(result.parsed_artifacts.keys()),
    }


def _payload_ok(task_id: str, payload: dict[str, Any]) -> bool:
    if task_id == "postclose_review":
        return bool(payload.get("ok"))
    if task_id == "premarket_plan":
        agent_stage = payload.get("stages", {}).get("agent_research", {})
        if isinstance(agent_stage, dict) and "ok" in agent_stage:
            return bool(agent_stage.get("ok"))
    return True


def _build_postclose_context(output_dir: Path) -> str:
    parts = []
    for name in ("plan.json", "state.json", "ledger.jsonl", "workflow_events.jsonl"):
        path = output_dir / name
        parts.append(f"## {name}")
        if not path.exists():
            parts.append("(missing)")
            continue
        parts.append(_read_text_tail(path))
    daily_reports = output_dir / "daily_reports"
    if daily_reports.is_dir():
        reports = sorted(daily_reports.glob("*.json"))[-3:]
        for path in reports:
            parts.append(f"## daily_reports/{path.name}")
            parts.append(_read_text_tail(path))
    return "\n\n".join(parts)


def _read_text_tail(path: Path, limit: int = 12000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"(unreadable: {exc})"
    if len(text) <= limit:
        return text
    return text[-limit:]


def _task_node_name(task_id: str) -> str:
    if task_id == "premarket_plan":
        return "盘前计划"
    if task_id == "postclose_review":
        return "盘后复盘"
    return task_id


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run one scheduled AlphaClaude Agent task")
    parser.add_argument("task_id", choices=sorted(VALID_SCHEDULED_AGENT_TASKS))
    parser.add_argument("--mode", default="paper", choices=["paper", "backtest", "live"])
    args = parser.parse_args(argv)
    result = run_scheduled_agent_task(args.task_id, mode=args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
