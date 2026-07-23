from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openalphastack.engine.clock import TradingClock
from openalphastack.engine.execution import ExecutionEngine
from openalphastack.engine.fast_lane import FastLane
from openalphastack.engine.ledger import Ledger
from openalphastack.engine.plan import PlanManager
from openalphastack.engine.state import EngineState
from openalphastack.tools.engine_status import format_plan_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def alpha_dir() -> Path:
    tmp_root = PROJECT_ROOT / "data" / "test_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_root / f"alpha_elasticity_{uuid.uuid4().hex}"
    tmp_path.mkdir(exist_ok=False)
    try:
        yield tmp_path
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def _engine_parts(path: Path, now: datetime):
    state = EngineState(str(path), 100000)
    plan = PlanManager(str(path))
    plan.set_sim_now(now)
    clock = TradingClock(mode="backtest", sim_start=now)
    ledger = Ledger(str(path))
    execution = ExecutionEngine(state, plan, ledger, mode="backtest", run_id="elastic_test")
    return state, plan, clock, ledger, execution


def _mock_feed(quotes: dict[str, dict]) -> MagicMock:
    feed = MagicMock()
    feed.current_day_data.return_value = quotes
    feed.get_index_quote.return_value = {"code": "000001", "price": 3200, "change_pct": 0.0}
    feed.get_history_up_to.return_value = None
    return feed


def _candidate(code: str, **overrides) -> dict:
    base = {
        "code": code,
        "priority": 1,
        "entry_max": 10.0,
        "entry_min": 9.0,
        "position_pct": 5.0,
        "stop_loss_pct": -5.0,
        "take_profit_pct": 10.0,
        "valid_until": "2025-03-20",
    }
    base.update(overrides)
    return base


def test_fast_lane_records_only_key_workflow_ticks():
    workflow = MagicMock()
    fast_lane = FastLane.__new__(FastLane)
    fast_lane.workflow = workflow

    fast_lane._record_key_tick([], False, "", ["300001"])
    workflow.record_node_finish.assert_not_called()

    fast_lane._record_key_tick(
        [{"event": "rule_signal_buy", "code": "300001"}],
        False,
        "",
        ["300001"],
    )

    workflow.record_node_finish.assert_called_once()
    kwargs = workflow.record_node_finish.call_args.kwargs
    assert kwargs["node_id"] == "intraday_event_stream"
    assert kwargs["input_refs"] == ["artifact.fastlane.tick", "account.state", "artifact.plan.json"]
    assert kwargs["output_payload"]["events"][0]["event"] == "rule_signal_buy"


def test_plan_uses_explicit_strategy_type_and_ignores_reasoning_keywords(alpha_dir):
    _state, plan, _clock, _ledger, _execution = _engine_parts(
        alpha_dir, datetime(2025, 3, 14, 9, 20)
    )

    plan.set_candidates([
        _candidate("300001", source="B"),
        _candidate("600001", strategy_type="defensive", reasoning="任意说明文字"),
        _candidate("300002", strategy_type="pullback", reasoning="任意说明文字"),
        _candidate("600999", reasoning="信息不足"),
        _candidate("600888", reasoning="高股息红利银行，低波动防御仓"),
    ])

    assert plan.get_candidate("300001")["strategy_type"] == "breakout"
    assert plan.get_candidate("300001")["confirm_after"] == "09:45"
    assert plan.get_candidate("600001")["strategy_type"] == "defensive"
    assert plan.get_candidate("300002")["strategy_type"] == "pullback"
    assert plan.get_candidate("600999")["strategy_type"] == "breakout"
    assert plan.get_candidate("600999")["confirm_after"] == "09:45"
    assert plan.get_candidate("600888")["strategy_type"] == "breakout"


def test_explicit_watch_only_is_preserved(alpha_dir):
    _state, plan, _clock, _ledger, _execution = _engine_parts(
        alpha_dir, datetime(2025, 3, 14, 9, 20)
    )

    plan.set_candidates([
        _candidate("600999", strategy_type="watch_only", reasoning="只观察不自动买")
    ])

    assert plan.get_candidate("600999")["strategy_type"] == "watch_only"


def test_watch_only_candidate_never_auto_buys(alpha_dir, monkeypatch):
    state, plan, clock, ledger, execution = _engine_parts(
        alpha_dir, datetime(2025, 3, 14, 10, 0)
    )
    monkeypatch.setattr("openalphastack.tools.signal_rules.scan_code", lambda _code, df=None: {"signals": []})
    plan.set_candidates([_candidate("600999", strategy_type="watch_only")])
    fast_lane = FastLane(
        state,
        plan,
        execution,
        clock,
        "backtest",
        ["600999"],
        data_feed=_mock_feed({"600999": {"code": "600999", "price": 9.8, "high": 10.0, "low": 9.5}}),
    )

    result = fast_lane.tick()
    entries = ledger.read_all()

    assert "600999" not in state.holdings
    assert not any(e.get("decision") == "open_position" for e in entries)
    assert any(e.get("decision") == "rejected_buy" and e.get("rule") == "watch_only" for e in entries)
    assert any(e.get("event") == "candidate_rejected" and e.get("rule") == "watch_only" for e in result["events"])


def test_breakout_candidate_waits_until_confirm_after(alpha_dir, monkeypatch):
    state, plan, clock, ledger, execution = _engine_parts(
        alpha_dir, datetime(2025, 3, 14, 9, 40)
    )
    monkeypatch.setattr("openalphastack.tools.signal_rules.scan_code", lambda _code, df=None: {"signals": []})
    plan.set_candidates([
        _candidate("300001", source="B", strategy_type="breakout", confirm_after="09:45")
    ])
    fast_lane = FastLane(
        state,
        plan,
        execution,
        clock,
        "backtest",
        ["300001"],
        data_feed=_mock_feed({"300001": {"code": "300001", "price": 9.8, "high": 10.0, "low": 9.5}}),
    )

    fast_lane.tick()

    assert "300001" not in state.holdings
    assert not any(e.get("decision") == "open_position" for e in ledger.read_all())


def test_daily_new_position_cap_rejects_excess_candidates(alpha_dir, monkeypatch):
    state, plan, clock, ledger, execution = _engine_parts(
        alpha_dir, datetime(2025, 3, 14, 10, 0)
    )
    monkeypatch.setattr("openalphastack.tools.signal_rules.scan_code", lambda _code, df=None: {"signals": []})
    plan._data["rules"]["daily_new_positions_limit"] = 1
    plan.set_candidates([
        _candidate("600001", strategy_type="defensive", priority=1),
        _candidate("600002", strategy_type="defensive", priority=1),
    ])
    fast_lane = FastLane(
        state,
        plan,
        execution,
        clock,
        "backtest",
        ["600001", "600002"],
        data_feed=_mock_feed({
            "600001": {"code": "600001", "price": 9.8, "high": 10.0, "low": 9.5},
            "600002": {"code": "600002", "price": 9.8, "high": 10.0, "low": 9.5},
        }),
    )

    fast_lane.tick()
    entries = ledger.read_all()

    assert len([e for e in entries if e.get("decision") == "open_position"]) == 1
    assert len(state.holdings) == 1
    assert any(e.get("decision") == "rejected_buy" and e.get("rule") == "daily_new_positions_limit" for e in entries)


def test_executed_candidate_buy_records_strategy_type(alpha_dir, monkeypatch):
    state, plan, clock, ledger, execution = _engine_parts(
        alpha_dir, datetime(2025, 3, 14, 10, 0)
    )
    monkeypatch.setattr("openalphastack.tools.signal_rules.scan_code", lambda _code, df=None: {"signals": []})
    plan.set_candidates([
        _candidate("300001", strategy_type="breakout", confirm_after="09:45")
    ])
    fast_lane = FastLane(
        state,
        plan,
        execution,
        clock,
        "backtest",
        ["300001"],
        data_feed=_mock_feed({"300001": {"code": "300001", "price": 9.8, "high": 10.0, "low": 9.5}}),
    )

    fast_lane.tick()
    open_entries = [e for e in ledger.read_all() if e.get("decision") == "open_position"]

    assert len(open_entries) == 1
    assert open_entries[0]["symbol"] == "300001"
    assert open_entries[0]["strategy_type"] == "breakout"
    assert open_entries[0]["signal_detail"].startswith("策略=breakout")


def test_plan_summary_shows_strategy_type(alpha_dir, monkeypatch):
    _state, plan, _clock, _ledger, _execution = _engine_parts(
        alpha_dir, datetime(2025, 3, 14, 10, 0)
    )
    plan.set_candidates([
        _candidate("300001", strategy_type="breakout", confirm_after="09:45")
    ])
    run = {
        "run_id": alpha_dir.name,
        "is_alive": True,
        "run_dir": str(alpha_dir),
    }
    monkeypatch.setattr("openalphastack.tools.engine_status.get_all_runs", lambda: [run])

    text = format_plan_text()

    assert "300001 breakout P1" in text
