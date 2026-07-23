from __future__ import annotations

import json
import shutil
import sys
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
from openalphastack.tools import engine_status

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))



def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture
def engine_dir() -> Path:
    tmp_root = PROJECT_ROOT / "data" / "test_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_root / f"openalphastack_engine_test_{uuid.uuid4().hex}"
    tmp_path.mkdir(exist_ok=False)
    plan_data = {
        "updated": "2025-03-14T09:30:00",
        "updated_by": "test",
        "market_bias": "neutral",
        "bias_confidence": 60,
        "bias_reasoning": "test",
        "holdings": {
            "600036": {"stop_loss": 40.50, "take_profit": 44.50},
            "300488": {"stop_loss": 41.50, "take_profit": 48.00},
            "002594": {"stop_loss": 270.00, "take_profit": 320.00},
        },
        "watchlist": [],
        "checklist": [],
        "rules": {
            "max_single_position_pct": 25.0,
            "max_total_position_pct": 80.0,
            "min_cash_reserve": 0.0,
            "stop_loss_mode": "hard",
        },
        "pending_orders": [],
        "position_cap_pct": 80.0,
        "preferred_sectors": [],
        "avoid_sectors": [],
        "emergency_triggers": {
            "market_drop_pct": 3.0,
            "single_stock_drop_pct": 5.0,
            "account_drawdown_pct": 10.0,
        },
        "buy_candidates": [
            {
                "code": "600036",
                "source": "C",
                "priority": 1,
                "cooldown_days": 1,
                "max_hold_days": 20,
                "entry_max": 44.00,
                "entry_min": 41.00,
                "stop_loss_pct": -5.0,
                "take_profit_pct": 8.0,
                "position_pct": 20.0,
                "stop_loss": 40.50,
                "take_profit": 44.50,
                "expires_after_days": 3,
                "valid_until": "2025-03-20",
                "position_limit_pct": 25.0,
                "max_shares": 500,
            },
            {
                "code": "300488",
                "source": "B",
                "priority": 2,
                "cooldown_days": 1,
                "max_hold_days": 5,
                "entry_max": 50.00,
                "entry_min": 38.00,
                "stop_loss_pct": -5.0,
                "take_profit_pct": 10.0,
                "position_pct": 15.0,
                "stop_loss": 41.50,
                "take_profit": 48.00,
                "expires_after_days": 3,
                "valid_until": "2025-03-20",
                "position_limit_pct": 15.0,
                "max_shares": 300,
            },
            {
                "code": "002594",
                "source": "C",
                "priority": 3,
                "cooldown_days": 2,
                "max_hold_days": 3,
                "entry_max": 999.00,
                "entry_min": 200.00,
                "stop_loss_pct": -5.0,
                "take_profit_pct": 10.0,
                "position_pct": 5.0,
                "stop_loss": 270.00,
                "take_profit": 320.00,
                "expires_after_days": 2,
                "valid_until": "2025-03-20",
                "position_limit_pct": 5.0,
                "max_shares": 100,
            },
        ],
        "holding_adjustments": [
            {
                "code": "600036",
                "t0_config": {
                    "preferred_direction": "forward",
                    "buy_trigger_price": 41.86,
                    "sell_target_pct": 2.0,
                    "stop_loss_pct": -1.0,
                    "max_shares_pct": 30,
                    "max_rounds": 2,
                    "breakout_price": 44.63,
                    "breakdown_price": 40.38,
                    "atr_pct": 3.0,
                    "enabled": True,
                },
            },
        ],
        "risk_report": {"rejected_candidates": [], "passed_count": 3, "rejected_count": 0},
        "cooldown": {},
        "today_stopped_out": [],
        "strategy_variant": {
            "name": "default",
            "source_b_max_pct": 20.0,
            "source_b_stop_pct": -8,
            "source_c_max_pct": 7.5,
            "source_c_stop_pct": -5,
            "max_single_position_pct": 25.0,
            "signal_min_confidence": 65,
            "signal_position_pct": 0.075,
            "max_total_position_pct": 80.0,
        },
    }
    state_data = {
        "initial_capital": 100000,
        "cash": 50000,
        "frozen_cash": 0,
        "holdings": {
            "600036": {
                "shares": 500,
                "available": 500,
                "locked_today": 0,
                "avg_cost": 42.50,
                "current_price": 43.00,
                "entry_date": "2025-03-03",
                "strategy": "",
                "stop_loss": 40.50,
                "take_profit": 44.50,
            },
            "300488": {
                "shares": 300,
                "available": 300,
                "locked_today": 0,
                "avg_cost": 43.50,
                "current_price": 42.00,
                "entry_date": "2025-03-10",
                "strategy": "",
                "stop_loss": 41.50,
                "take_profit": 48.00,
            },
            "002594": {
                "shares": 100,
                "available": 100,
                "locked_today": 0,
                "avg_cost": 300.00,
                "current_price": 280.00,
                "entry_date": "2025-03-10",
                "strategy": "scout",
                "stop_loss": 270.00,
                "take_profit": 320.00,
            },
        },
        "total_commission": 25.0,
        "total_stamp_duty": 11.0,
        "nav_curve": [],
        "data_time": "2025-03-14 09:30:00",
        "trade_count": 3,
        "win_count": 1,
    }
    try:
        _write_json(tmp_path / "plan.json", plan_data)
        _write_json(tmp_path / "state.json", state_data)
        (tmp_path / "ledger.jsonl").write_text("", encoding="utf-8")
        yield tmp_path
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.fixture
def engine_parts(engine_dir: Path):
    state = EngineState(str(engine_dir), 100000)
    plan = PlanManager(str(engine_dir))
    plan.set_sim_now(datetime(2025, 3, 14, 9, 35))
    clock = TradingClock(mode="backtest", sim_start=datetime(2025, 3, 14, 9, 35))
    ledger = Ledger(str(engine_dir))
    execution = ExecutionEngine(state, plan, ledger, mode="backtest")
    return state, plan, clock, ledger, execution


def _mock_feed(quotes: dict[str, dict]) -> MagicMock:
    feed = MagicMock()
    feed.current_day_data.return_value = quotes
    feed.get_index_quote.return_value = {"code": "000001", "price": 3200, "change_pct": 0.0}
    feed.get_history_up_to.return_value = None
    return feed


def test_execution_engine_notifies_through_injected_callback(engine_parts):
    state, plan, _clock, ledger, _execution = engine_parts
    calls = []
    execution = ExecutionEngine(
        state,
        plan,
        ledger,
        mode="backtest",
        run_id="test_run",
        notify_trade_func=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    trade = execution.execute_buy("000001", 100, 10.0, reasoning="test buy")

    assert trade["status"] == "executed"
    assert calls
    assert calls[0][0][:5] == ("buy", "000001", "", 10.0, 100)
    assert calls[0][1]["run_id"] == "test_run"


def test_execute_buy_ledger_contains_chart_risk_fields(engine_parts):
    _state, _plan, _clock, ledger, execution = engine_parts

    execution.execute_buy(
        "000002",
        100,
        12.34,
        stop_loss=11.11,
        take_profit=14.56,
        reasoning="chart fields",
    )

    entry = ledger.read_all()[-1]
    assert entry["decision"] == "open_position"
    assert entry["symbol"] == "000002"
    assert entry["avg_cost"] == 12.34
    assert entry["stop_loss"] == 11.11
    assert entry["take_profit"] == 14.56


def test_new_buy_is_locked_until_t1_release(engine_parts):
    state, _plan, _clock, _ledger, execution = engine_parts

    trade = execution.execute_buy("600519", 100, 100.0, reasoning="test buy")

    assert trade["status"] == "executed"
    holding = state.holdings["600519"]
    assert holding["shares"] == 100
    assert holding["locked_today"] == 100
    assert holding["available"] == 0

    sell = execution.execute_sell("600519", 100, 101.0, reason="same day sell")
    assert sell["error"] == "无 600519 持仓或无可卖股数"
    assert state.holdings["600519"]["shares"] == 100


def test_emergency_tier_persists_across_fast_lane_restart(engine_parts):
    state, plan, clock, ledger, execution = engine_parts
    state.update_quote("300488", 41.0)
    state.update_quote("002594", 300.0)
    quotes = {
        "300488": {"code": "300488", "price": 41.0},
        "000001": {"code": "000001", "price": 3200},
    }

    first = FastLane(
        state, plan, execution, clock, "paper",
        ["300488"], data_feed=_mock_feed(quotes),
    )
    triggered, reason = first._check_emergency(quotes)

    assert triggered is True
    assert "300488" in reason

    restarted = FastLane(
        state, plan, execution, clock, "paper",
        ["300488"], data_feed=_mock_feed(quotes),
    )
    triggered_again, reason_again = restarted._check_emergency(quotes)

    assert triggered_again is False
    assert reason_again == ""


def test_stop_loss_triggers_sell_and_cooldown(engine_parts):
    _state, plan, _clock, ledger, execution = engine_parts

    triggers = execution.check_stop_triggers({
        "300488": {"code": "300488", "price": 41.00, "high": 42.00, "low": 40.80},
    })

    assert any(t["event"] == "stop_loss_hit" and t["code"] == "300488" for t in triggers)
    assert any(entry["decision"] == "close_position" for entry in ledger.read_all())
    assert plan.is_on_cooldown("300488")
    assert "300488" in plan._data["today_stopped_out"]


def test_take_profit_triggers_sell_and_cooldown(engine_parts):
    _state, plan, _clock, ledger, execution = engine_parts

    triggers = execution.check_stop_triggers({
        "600036": {"code": "600036", "price": 45.00, "high": 45.50, "low": 44.50},
    })

    assert any(t["event"] == "take_profit_hit" and t["code"] == "600036" for t in triggers)
    assert any(entry["decision"] == "close_position" for entry in ledger.read_all())
    assert plan.is_on_cooldown("600036")


def test_time_condition_close_uses_mocked_backtest_feed(engine_parts, monkeypatch):
    state, plan, clock, ledger, execution = engine_parts
    monkeypatch.setattr("openalphastack.tools.signal_rules.scan_code", lambda _code, df=None: {"signals": []})
    quotes = {
        "600036": {"code": "600036", "price": 43.00, "high": 43.50, "low": 42.80},
        "300488": {"code": "300488", "price": 42.00, "high": 43.00, "low": 41.80},
        "002594": {"code": "002594", "price": 280.00, "high": 285.00, "low": 278.00},
    }
    fast_lane = FastLane(
        state, plan, execution, clock, "backtest",
        ["600036", "300488", "002594"], data_feed=_mock_feed(quotes),
    )

    result = fast_lane.tick()

    assert any(e["event"] == "max_hold_close" and e["code"] == "002594" for e in result["events"])
    assert not any(e.get("code") == "300488" and e["event"] == "max_hold_close" for e in result["events"])
    assert "600036" in state.holdings
    assert plan.is_on_cooldown("002594")
    assert any(
        entry["decision"] == "close_position" and entry["symbol"] == "002594"
        for entry in ledger.read_all()
    )


def test_t0_forward_cycle_uses_point_price_triggers(engine_parts):
    state, plan, clock, ledger, execution = engine_parts
    fast_lane = FastLane(
        state, plan, execution, clock, "backtest",
        ["600036", "300488", "002594"], data_feed=_mock_feed({}),
    )
    fast_lane._load_t0_configs()

    assert "600036" in fast_lane._t0_trackers
    tracker = fast_lane._t0_trackers["600036"]
    assert tracker.enabled
    assert tracker.preferred_direction == "forward"

    events = []
    fast_lane._run_t0_cycle(
        {"600036": {"code": "600036", "price": 41.86, "volume_ratio": 1.0}},
        "2025-03-14 10:01:00",
        events,
    )

    tracker = fast_lane._t0_trackers["600036"]
    assert tracker.state == "active_buy"
    assert any(e["event"] == "t0_entry" and e["code"] == "600036" for e in events)
    assert any(
        entry["decision"] == "open_position" and entry["symbol"] == "600036"
        for entry in ledger.read_all()
    )

    events.clear()
    fast_lane._run_t0_cycle(
        {"600036": {"code": "600036", "price": tracker.t0_target_price, "volume_ratio": 1.0}},
        "2025-03-14 10:05:00",
        events,
    )

    assert tracker.state == "idle"
    assert any(e["event"] == "t0_complete" and e["code"] == "600036" for e in events)


def test_cooldown_prevents_rebuy_with_mocked_feed(engine_parts, monkeypatch):
    state, plan, clock, ledger, execution = engine_parts
    monkeypatch.setattr("openalphastack.tools.signal_rules.scan_code", lambda _code, df=None: {"signals": []})
    plan.mark_stopped_out("300488", cooldown_hours=24)
    state._data["holdings"].pop("300488")
    quotes = {
        "300488": {"code": "300488", "price": 43.00, "high": 44.00, "low": 42.50},
    }
    fast_lane = FastLane(
        state, plan, execution, clock, "backtest",
        ["600036", "300488", "002594"], data_feed=_mock_feed(quotes),
    )

    result = fast_lane.tick()

    assert not any(e.get("code") == "300488" and "buy" in e.get("event", "") for e in result["events"])
    assert not any(
        entry["decision"] == "open_position" and entry["symbol"] == "300488"
        for entry in ledger.read_all()
    )


def test_bucket_allocation_fixture_matches_expected_exposures(engine_parts):
    state, _plan, _clock, _ledger, _execution = engine_parts

    assert state.holdings["600036"]["shares"] * state.holdings["600036"]["current_price"] == 21500
    assert state.holdings["300488"]["shares"] * state.holdings["300488"]["current_price"] == 12600
    assert state.holdings["002594"]["shares"] * state.holdings["002594"]["current_price"] == 28000
    assert state.holdings["002594"]["shares"] * state.holdings["002594"]["current_price"] > 20000


def test_format_status_text_shows_observing_run_as_active_waiting():
    runs = [
        {
            "run_id": "paper_2026-05-25T09-00-00",
            "mode": "paper",
            "phase": "observing",
            "is_alive": True,
            "data_time": "2026-05-25 09:00:00",
            "plan_updated": "",
            "initial_capital": 100000,
            "cash": 100000,
            "nav": 100000,
            "total_value": 100000,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "holdings_value": 0,
            "unrealized_pnl": 0,
            "holdings": {},
            "trade_count": 0,
            "win_count": 0,
            "win_rate": 0,
            "market_bias": "neutral",
            "bias_confidence": 0,
            "candidates_count": 0,
            "pending_orders_count": 0,
            "watchlist_count": 0,
            "adjustments_count": 0,
            "engine_meta": {"observation_mode": True},
            "observation_reason": "周日休市; waiting for next trading session",
            "day_pnl": 0,
            "day_pnl_pct": 0,
            "max_drawdown": 0,
            "today_trades": 0,
            "total_commission": 0,
            "total_stamp_duty": 0,
            "cooldown_count": 0,
            "cooldown_codes": [],
            "stopped_out_count": 0,
            "stopped_out_codes": [],
            "emergency_tiers": {},
            "rules": {},
        }
    ]

    text = engine_status.format_status_text(runs)

    assert "🟢 运行中: 1 个 (paper)" in text
    assert "休市待机" in text
    assert "待机原因: 周日休市; waiting for next trading session" in text


def test_phase_label_keeps_dead_observation_run_stopped():
    assert engine_status._phase_label("已停止") == "已停止"
