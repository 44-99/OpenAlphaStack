from __future__ import annotations

import json
import shutil
import uuid
from datetime import date
from pathlib import Path

import pytest

from alphaclaude import paths
from alphaclaude.engine.agent_task_runner import AgentTaskResult
from alphaclaude.engine import scheduled_agent_task as task_module


@pytest.fixture
def output_root(monkeypatch):
    root = paths.PROJECT_ROOT / "data" / "test_tmp" / f"scheduled_agent_{uuid.uuid4().hex}"
    output = root / "output"
    output.mkdir(parents=True)
    monkeypatch.setattr(task_module, "_output_base", lambda: output)
    try:
        yield output
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_scheduled_run_id_uses_task_and_date():
    assert task_module.scheduled_run_id("premarket_plan", date(2026, 6, 20)) == "agent_2026-06-20_premarket_plan"


def test_premarket_task_runs_pipeline_and_writes_agent_state(output_root, monkeypatch):
    calls = []

    class FakePipeline:
        def __init__(self, state, plan, ledger, clock, output_dir, mode):
            self.output_dir = Path(output_dir)
            calls.append({
                "output_dir": output_dir,
                "mode": mode,
                "state": state,
                "plan": plan,
                "ledger": ledger,
                "clock": clock,
            })

        def run_full(self):
            (self.output_dir / "workflow_events.jsonl").write_text(
                json.dumps({
                    "event_id": "wf_agent",
                    "run_id": self.output_dir.name,
                    "phase": "premarket",
                    "node_id": "agent_research",
                    "node_name": "自主 Agent 研究",
                    "status": "success",
                    "started_at": "",
                    "ended_at": "",
                    "duration_ms": 0,
                    "input_refs": [],
                    "output_refs": [],
                    "summary": "ok",
                    "error": "",
                    "artifact_dir": "",
                }) + "\n",
                encoding="utf-8",
            )
            return {"stages": {"agent_research": {"ok": True}}}

    monkeypatch.setattr(task_module, "OvernightPipeline", FakePipeline)

    result = task_module.run_scheduled_agent_task("premarket_plan", mode="paper", today=date(2026, 6, 20))

    assert result["ok"] is True
    assert result["task_id"] == "premarket_plan"
    assert result["run_id"] == "agent_2026-06-20_premarket_plan"
    assert calls[0]["mode"] == "paper"
    state = json.loads((output_root / result["run_id"] / "state.json").read_text(encoding="utf-8"))
    assert state["engine_meta"]["mode"] == "agent"
    assert state["engine_meta"]["agent_task_id"] == "premarket_plan"
    assert state["engine_meta"]["status"] == "completed"
    events = [
        json.loads(line)
        for line in (output_root / result["run_id"] / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["node_id"] == "agent_research" and event["status"] == "success" for event in events)


def test_premarket_agent_failure_marks_scheduled_task_failed(output_root, monkeypatch):
    class FakePipeline:
        def __init__(self, state, plan, ledger, clock, output_dir, mode):
            self.output_dir = Path(output_dir)

        def run_full(self):
            (self.output_dir / "workflow_events.jsonl").write_text(
                json.dumps({
                    "event_id": "wf_agent",
                    "run_id": self.output_dir.name,
                    "phase": "premarket",
                    "node_id": "agent_research",
                    "node_name": "自主 Agent 研究",
                    "status": "warning",
                    "started_at": "",
                    "ended_at": "",
                    "duration_ms": 0,
                    "input_refs": [],
                    "output_refs": [],
                    "summary": "agent failed",
                    "error": "",
                    "artifact_dir": "",
                }) + "\n",
                encoding="utf-8",
            )
            return {"stages": {"agent_research": {"ok": False, "error": "agent failed"}}}

    monkeypatch.setattr(task_module, "OvernightPipeline", FakePipeline)

    result = task_module.run_scheduled_agent_task("premarket_plan", mode="paper", today=date(2026, 6, 20))

    assert result["ok"] is False
    state = json.loads((output_root / result["run_id"] / "state.json").read_text(encoding="utf-8"))
    assert state["engine_meta"]["status"] == "failed"


def test_postclose_task_runs_agent_and_records_warning(output_root, monkeypatch):
    captured = {}

    def fake_run_postclose(self, review_context=""):
        captured["context"] = review_context
        artifacts_dir = Path(self.output_dir) / "agent_runs" / "postclose_review"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        return AgentTaskResult(
            task_id="postclose_review",
            ok=True,
            returncode=0,
            artifacts_dir=artifacts_dir,
            stdout="ok",
            stderr="",
            parsed_artifacts={"strategy_attribution": {"trades": []}},
            audit_warnings=["events.jsonl missing"],
            agent_events=[],
        )

    monkeypatch.setattr(task_module.AgentTaskRunner, "run_postclose_review", fake_run_postclose)

    run_dir = output_root / "agent_2026-06-20_postclose_review"
    run_dir.mkdir()
    (run_dir / "plan.json").write_text('{"market_bias":"neutral"}', encoding="utf-8")
    (run_dir / "ledger.jsonl").write_text('{"symbol":"600000","decision":"buy"}\n', encoding="utf-8")

    result = task_module.run_scheduled_agent_task("postclose_review", mode="paper", today=date(2026, 6, 20))

    assert result["ok"] is True
    assert "plan.json" in captured["context"]
    assert "ledger.jsonl" in captured["context"]
    events = [
        json.loads(line)
        for line in (output_root / result["run_id"] / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["node_id"] == "trade_attribution" and event["status"] == "warning" for event in events)


def test_postclose_agent_failure_marks_scheduled_task_failed(output_root, monkeypatch):
    def fake_run_postclose(self, review_context=""):
        artifacts_dir = Path(self.output_dir) / "agent_runs" / "postclose_review"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        return AgentTaskResult(
            task_id="postclose_review",
            ok=False,
            returncode=1,
            artifacts_dir=artifacts_dir,
            stdout="",
            stderr="boom",
            parsed_artifacts={},
            audit_warnings=[],
            agent_events=[],
            error="boom",
        )

    monkeypatch.setattr(task_module.AgentTaskRunner, "run_postclose_review", fake_run_postclose)

    result = task_module.run_scheduled_agent_task("postclose_review", mode="paper", today=date(2026, 6, 20))

    assert result["ok"] is False
    state = json.loads((output_root / result["run_id"] / "state.json").read_text(encoding="utf-8"))
    assert state["engine_meta"]["status"] == "failed"
    events = [
        json.loads(line)
        for line in (output_root / result["run_id"] / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["node_id"] == "trade_attribution" and event["status"] == "warning" for event in events)


def test_unknown_scheduled_agent_task_is_rejected(output_root):
    with pytest.raises(task_module.UnknownScheduledAgentTask):
        task_module.run_scheduled_agent_task("not_a_task", today=date(2026, 6, 20))
