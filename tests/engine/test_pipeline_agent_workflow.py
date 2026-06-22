from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alphaclaude.engine import pipeline as pipeline_module
from alphaclaude.engine.agent_task_runner import AgentTaskResult
from alphaclaude.engine.pipeline import OvernightPipeline

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakePlan:
    def __init__(self):
        self._data = {
            "market_bias": "neutral",
            "bias_confidence": 50,
            "bias_reasoning": "",
            "position_cap_pct": 80,
            "preferred_sectors": [],
            "avoid_sectors": [],
            "buy_candidates": [],
            "holding_adjustments": [],
            "risk_report": {"passed_count": 0, "rejected_count": 0},
            "rules": {"max_single_position_pct": 25.0, "max_total_position_pct": 80.0},
        }

    def set_market_bias(self, bias, confidence, reasoning, position_cap=None, preferred=None, avoid=None):
        self._data["market_bias"] = bias
        self._data["bias_confidence"] = confidence
        self._data["bias_reasoning"] = reasoning
        if position_cap is not None:
            self._data["position_cap_pct"] = position_cap
        if preferred is not None:
            self._data["preferred_sectors"] = preferred
        if avoid is not None:
            self._data["avoid_sectors"] = avoid

    def set_candidates(self, candidates):
        self._data["buy_candidates"] = candidates

    def set_adjustments(self, adjustments):
        self._data["holding_adjustments"] = adjustments

    def load(self):
        return dict(self._data)


@pytest.fixture
def pipeline_dir() -> Path:
    tmp_root = PROJECT_ROOT / "data" / "test_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    path = tmp_root / f"pipeline_agent_{uuid.uuid4().hex}"
    path.mkdir(exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _pipeline(path: Path):
    state = MagicMock()
    state.total_value = 100000
    state.holdings = {}
    state.load.return_value = {"cash": 100000, "holdings": {}}

    ledger = MagicMock()
    clock = MagicMock()
    clock.now.return_value = datetime(2025, 3, 14, 8, 30)

    return OvernightPipeline(state, FakePlan(), ledger, clock, str(path), mode="backtest")


def test_agent_workflow_imports_plan_draft_then_runs_risk_gate(pipeline_dir, monkeypatch):
    pipeline = _pipeline(pipeline_dir)
    risk_calls = []
    artifacts_dir = pipeline_dir / "agent_runs" / "premarket_plan"
    draft = {
        "market_bias": "bearish",
        "bias_confidence": 63,
        "bias_reasoning": "情绪退潮",
        "position_cap_pct": 20,
        "preferred_sectors": ["现金"],
        "avoid_sectors": ["高位题材"],
        "buy_candidates": [
            {
                "code": "600036",
                "entry_max": 40,
                "stop_loss_pct": -4,
                "take_profit_pct": 8,
                "position_pct": 5,
            }
        ],
        "holding_adjustments": [{"code": "300400", "action": "close"}],
    }

    class FakeRunner:
        def __init__(self, output_dir, run_id, timeout):
            assert Path(output_dir) == pipeline_dir
            assert run_id == pipeline.run_id
            assert timeout == 123

        def run_premarket_plan(self, market_snapshot="", account_summary=""):
            assert market_snapshot == "MARKET"
            assert "总资产" in account_summary
            return AgentTaskResult(
                task_id="premarket_plan",
                ok=True,
                returncode=0,
                artifacts_dir=artifacts_dir,
                stdout="ok",
                stderr="",
                parsed_artifacts={"plan_draft": draft},
                audit_warnings=[],
                agent_events=[{"task_id": "market_intel", "status": "success"}],
            )

    def fake_risk_validation():
        risk_calls.append("risk")
        pipeline.plan._data["risk_report"] = {"passed_count": 1, "rejected_count": 0}
        return {"stage": "risk", "passed": 1, "rejected": 0}

    monkeypatch.setattr(pipeline_module, "AGENT_WORKFLOW_TIMEOUT", 123)
    monkeypatch.setattr(pipeline_module, "AgentTaskRunner", FakeRunner)
    monkeypatch.setattr(pipeline, "_fetch_market_snapshot", lambda: "MARKET")
    monkeypatch.setattr(pipeline, "run_risk_validation", fake_risk_validation)

    result = pipeline.run_full()

    assert result["stages"]["agent_research"]["ok"] is True
    assert result["stages"]["agent_research"]["imported"]["candidates"] == 1
    assert result["stages"]["agent_research"]["agent_events"] == 1
    assert result["stages"]["agent_research"]["audit_warnings"] == []
    assert risk_calls == ["risk"]
    assert pipeline.plan._data["market_bias"] == "bearish"
    assert pipeline.plan._data["position_cap_pct"] == 20
    assert pipeline.plan._data["buy_candidates"][0]["code"] == "600036"
    assert pipeline.plan._data["holding_adjustments"][0]["action"] == "close"

    events = [
        json.loads(line)
        for line in (pipeline_dir / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["node_id"] == "agent_research" and event["status"] == "success" for event in events)


def test_agent_workflow_audit_warnings_mark_workflow_warning(pipeline_dir, monkeypatch):
    pipeline = _pipeline(pipeline_dir)
    artifacts_dir = pipeline_dir / "agent_runs" / "premarket_plan"

    class FakeRunner:
        def __init__(self, output_dir, run_id, timeout):
            pass

        def run_premarket_plan(self, market_snapshot="", account_summary=""):
            return AgentTaskResult(
                task_id="premarket_plan",
                ok=True,
                returncode=0,
                artifacts_dir=artifacts_dir,
                stdout="ok",
                stderr="",
                parsed_artifacts={"plan_draft": {"market_bias": "neutral", "buy_candidates": []}},
                audit_warnings=["events.jsonl missing"],
                agent_events=[],
            )

    monkeypatch.setattr(pipeline_module, "AgentTaskRunner", FakeRunner)
    monkeypatch.setattr(pipeline, "_fetch_market_snapshot", lambda: "MARKET")
    monkeypatch.setattr(pipeline, "run_risk_validation", lambda: {"stage": "risk", "passed": 0, "rejected": 0})

    result = pipeline.run_full()

    events = [
        json.loads(line)
        for line in (pipeline_dir / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    warning = next(
        event for event in events
        if event["node_id"] == "agent_research" and event["status"] == "warning"
    )

    assert result["stages"]["agent_research"]["audit_warnings"] == ["events.jsonl missing"]
    assert "审计告警 1 条" in warning["summary"]


def test_agent_workflow_failed_agent_marks_workflow_warning(pipeline_dir, monkeypatch):
    pipeline = _pipeline(pipeline_dir)
    artifacts_dir = pipeline_dir / "agent_runs" / "premarket_plan"

    class FakeRunner:
        def __init__(self, output_dir, run_id, timeout):
            pass

        def run_premarket_plan(self, market_snapshot="", account_summary=""):
            return AgentTaskResult(
                task_id="premarket_plan",
                ok=False,
                returncode=1,
                artifacts_dir=artifacts_dir,
                stdout="",
                stderr="failed",
                parsed_artifacts={},
                audit_warnings=[],
                agent_events=[],
                error="failed",
            )

    monkeypatch.setattr(pipeline_module, "AgentTaskRunner", FakeRunner)
    monkeypatch.setattr(pipeline, "_fetch_market_snapshot", lambda: "MARKET")
    monkeypatch.setattr(pipeline, "run_risk_validation", lambda: {"stage": "risk", "passed": 0, "rejected": 0})

    result = pipeline.run_full()

    events = [
        json.loads(line)
        for line in (pipeline_dir / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    warning = next(
        event for event in events
        if event["node_id"] == "agent_research" and event["status"] == "warning"
    )

    assert result["stages"]["agent_research"]["ok"] is False
    assert "未成功" in warning["summary"]
