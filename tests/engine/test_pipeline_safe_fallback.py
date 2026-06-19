from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from alphaclaude.engine import pipeline as pipeline_module
from alphaclaude.engine.pipeline import OvernightPipeline
from alphaclaude.tools import llm_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pipeline_dir() -> Path:
    tmp_root = PROJECT_ROOT / "data" / "test_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    path = tmp_root / f"pipeline_safe_{uuid.uuid4().hex}"
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

    plan = MagicMock()
    ledger = MagicMock()
    clock = MagicMock()
    clock.now.return_value = datetime(2025, 3, 14, 8, 30)

    return OvernightPipeline(state, plan, ledger, clock, str(path), mode="backtest")


def test_emergency_fallback_holds_when_text_is_unstructured(pipeline_dir, monkeypatch):
    pipeline = _pipeline(pipeline_dir)

    def raise_tool(*_args, **_kwargs):
        raise RuntimeError("tool use failed")

    monkeypatch.setattr(llm_client, "call_with_tool", raise_tool)
    monkeypatch.setattr(llm_client, "call_text", lambda *_args, **_kwargs: "无法结构化，建议先观望。")

    result = pipeline.launch_emergency("指数快速下跌")

    payload = json.loads(result)
    assert payload == [{"action": "hold", "reasoning": "无法结构化，建议先观望。"}]
    pipeline.ledger.append.assert_called_once()
    assert pipeline.ledger.append.call_args.args[0]["action"] == "hold"


def test_launch_emergency_does_not_send_duplicate_alert(pipeline_dir, monkeypatch):
    pipeline = _pipeline(pipeline_dir)
    alerts = []

    pipeline_module.notify_alert = lambda *args: alerts.append(args)
    monkeypatch.setattr(llm_client, "call_with_tool", lambda *_args, **_kwargs: [{"action": "hold", "reasoning": "test"}])

    pipeline.launch_emergency("300263 下跌5.0%")

    assert alerts == []


def test_run_full_records_market_artifact_and_agent_research_inputs(pipeline_dir, monkeypatch):
    pipeline = _pipeline(pipeline_dir)
    pipeline.plan._data = {
        "market_bias": "neutral",
        "buy_candidates": [],
        "risk_report": {"passed_count": 0, "rejected_count": 0},
    }
    artifacts_dir = pipeline_dir / "agent_runs" / "premarket_plan"

    class FakeRunner:
        def __init__(self, output_dir, run_id, timeout):
            assert Path(output_dir) == pipeline_dir
            assert run_id == pipeline.run_id

        def run_premarket_plan(self, market_snapshot="", account_summary=""):
            assert market_snapshot == "MARKET SNAPSHOT"
            assert "总资产" in account_summary
            from alphaclaude.engine.agent_task_runner import AgentTaskResult
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

    monkeypatch.setattr(pipeline, "_fetch_market_snapshot", lambda: "MARKET SNAPSHOT")
    monkeypatch.setattr(pipeline_module, "AgentTaskRunner", FakeRunner)
    monkeypatch.setattr(pipeline, "run_risk_validation", lambda: {"stage": "risk", "passed": 0, "rejected": 0})

    pipeline.run_full()

    events = [
        json.loads(line)
        for line in (pipeline_dir / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    market_event = next(
        event for event in events
        if event["node_id"] == "market_snapshot" and event["status"] == "success"
    )
    market_output = json.loads(
        (pipeline_dir / market_event["artifact_dir"] / "output.json").read_text(encoding="utf-8")
    )
    agent_research = next(
        event for event in events
        if event["node_id"] == "agent_research" and event["status"] == "running"
    )

    assert market_output["market_snapshot"] == "MARKET SNAPSHOT"
    assert agent_research["input_refs"] == ["artifact.market.snapshot", "account.state", "rule.skills"]


def test_call_text_safe_does_not_set_pipeline_token_cap(pipeline_dir, monkeypatch):
    pipeline = _pipeline(pipeline_dir)
    calls = []

    def fake_call_text(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "FULL TEXT"

    monkeypatch.setattr(llm_client, "call_text", fake_call_text)

    assert pipeline._call_text_safe("PROMPT", "Label") == "FULL TEXT"
    assert calls == [("PROMPT", {"model": None})]
