from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from openalphastack.engine import T0Tracker

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def support_dir() -> Path:
    tmp_root = PROJECT_ROOT / "data" / "test_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_root / f"openalphastack_support_test_{uuid.uuid4().hex}"
    tmp_path.mkdir(exist_ok=False)
    try:
        yield tmp_path
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_t0_tracker_loads_config_and_resets_runtime_state():
    tracker = T0Tracker("600036")

    tracker.load_config({
        "enabled": True,
        "preferred_direction": "reverse",
        "max_shares_pct": 35,
        "buy_trigger_price": 41.86,
        "sell_target_pct": 1.8,
        "stop_loss_pct": -1.2,
        "max_rounds": 3,
        "breakout_price": 44.63,
        "breakdown_price": 40.38,
        "atr_pct": 2.5,
    }, available_shares=550)

    assert tracker.enabled is True
    assert tracker.preferred_direction == "reverse"
    assert tracker.max_shares == 100
    assert tracker.buy_trigger_price == 41.86
    assert tracker.max_rounds == 3

    tracker.rounds_done = 2
    tracker.state = "active_buy"
    tracker.t0_shares = 100
    tracker.t0_entry_price = 42.0
    tracker.t0_stop_price = 41.0
    tracker.t0_target_price = 43.0
    tracker.paused_until = "10:30"

    tracker.reset_day()

    assert tracker.rounds_done == 0
    assert tracker.state == "idle"
    assert tracker.t0_shares == 0
    assert tracker.t0_entry_price == 0.0
    assert tracker.paused_until == ""
