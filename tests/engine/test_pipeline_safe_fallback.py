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


def test_direction_fallback_infers_conservative_bias(pipeline_dir):
    pipeline = _pipeline(pipeline_dir)

    result = pipeline._parse_direction_fallback("市场偏空，风险偏高，总仓位20%。")

    assert result == [{
        "bias": "bearish",
        "confidence": 50,
        "bias_reasoning": "市场偏空，风险偏高，总仓位20%。",
        "position_cap": 20,
        "prefer_sectors": [],
        "avoid_sectors": [],
    }]


def test_bull_bear_debate_uses_safe_text_fallback(pipeline_dir, monkeypatch):
    pipeline = _pipeline(pipeline_dir)
    monkeypatch.setattr(pipeline, "_call_text_safe", lambda _prompt, label, **kw: f"{label} text")
    monkeypatch.setattr(pipeline, "_build_bull_prompt", lambda *_args: "bull prompt")
    monkeypatch.setattr(pipeline, "_build_bear_prompt", lambda *_args: "bear prompt")
    monkeypatch.setattr(pipeline, "_build_risk_prompt", lambda *_args: "risk prompt")

    def raise_tool(*_args, **_kwargs):
        raise RuntimeError("tool use failed")

    monkeypatch.setattr(llm_client, "call_with_tool", raise_tool)
    monkeypatch.setattr(
        llm_client,
        "call_text",
        lambda *_args, **_kwargs: json.dumps([{
            "code": "600036",
            "source": "B",
            "priority": 1,
            "entry_max": 43.5,
            "stop_loss_pct": -5,
            "take_profit_pct": 8,
            "position_pct": 10,
            "reasoning": "fallback candidate",
        }]),
    )

    candidates, trace = pipeline._run_bull_bear_debate({}, {"bias": "neutral"})

    assert candidates == [{
        "code": "600036",
        "source": "B",
        "reasoning": "fallback candidate",
        "priority": 1,
        "entry_max": 43.5,
        "stop_loss_pct": -5.0,
        "take_profit_pct": 8.0,
        "position_pct": 10.0,
    }]
    assert "BULL" in trace


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

    monkeypatch.setattr(pipeline_module, "_notify", True)
    pipeline_module.notify_alert = lambda *args: alerts.append(args)
    monkeypatch.setattr(llm_client, "call_with_tool", lambda *_args, **_kwargs: [{"action": "hold", "reasoning": "test"}])

    pipeline.launch_emergency("300263 下跌5.0%")

    assert alerts == []
