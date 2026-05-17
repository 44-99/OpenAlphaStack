from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from alphaclaude.engine import paper as paper_module
from alphaclaude.engine.paper import PaperEngine
from alphaclaude.tools.engine_status import _is_pid_alive

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now


@pytest.fixture
def output_base(monkeypatch) -> Path:
    tmp_root = PROJECT_ROOT / "data" / "test_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    path = tmp_root / f"paper_schedule_{uuid.uuid4().hex}"
    path.mkdir(exist_ok=False)
    monkeypatch.setattr(paper_module, "OUTPUT_BASE", str(path))
    monkeypatch.setattr(paper_module, "_notify", False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_new_paper_run_without_plan_is_not_actionable(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])

    assert not engine._has_actionable_plan_for_today()
    assert engine.state.load()["engine_meta"]["process_id"] == paper_module.os.getpid()

    engine._set_observation_mode(True, "trading hours without a pre-market plan")
    meta = engine.state.load()["engine_meta"]

    assert meta["observation_mode"] is True
    assert "pre-market plan" in meta["observation_reason"]


def test_generated_plan_makes_paper_run_actionable(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.plan.set_sim_now(datetime(2026, 5, 13, 8, 30))
    engine.plan.set_market_bias(
        "neutral",
        55,
        "盘前计划已生成，但今天不主动开仓。",
        position_cap=30,
    )

    assert engine._has_actionable_plan_for_today()

    engine._set_observation_mode(False)
    meta = engine.state.load()["engine_meta"]

    assert meta["observation_mode"] is False
    assert meta["observation_reason"] == ""


def test_non_trading_day_premarket_sends_skip_notice(output_base, monkeypatch):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.clock = FakeClock(datetime(2026, 5, 17, 8, 30))  # Sunday
    calls = []

    monkeypatch.setattr(paper_module, "_notify", True)
    monkeypatch.setattr(
        paper_module,
        "notify_non_trading_premarket",
        lambda *args: calls.append(args),
    )

    assert engine._is_non_trading_premarket_window()

    engine._handle_non_trading_premarket()

    meta = engine.state.load()["engine_meta"]
    assert meta["observation_mode"] is True
    assert "周日休市" in meta["observation_reason"]
    assert engine.state.load()["data_time"] == "2026-05-17 08:30:00"
    assert calls == [
        (
            engine.run_id,
            "2026-05-17",
            "周日休市",
            engine.state.total_value,
            0,
        )
    ]


def test_trading_day_premarket_is_not_closed_market_notice(output_base, monkeypatch):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.clock = FakeClock(datetime(2026, 5, 18, 8, 30))  # Monday

    monkeypatch.setattr(paper_module, "is_trading_day", lambda _day: True)

    assert not engine._is_non_trading_premarket_window()


def test_weekday_market_holiday_sends_skip_notice(output_base, monkeypatch):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.clock = FakeClock(datetime(2026, 5, 18, 8, 30))  # Monday

    monkeypatch.setattr(paper_module, "is_trading_day", lambda _day: False)

    assert engine._is_non_trading_premarket_window()
    assert engine._non_trading_day_reason() == "交易所休市"


def test_non_trading_day_notice_can_recover_after_premarket_window(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.clock = FakeClock(datetime(2026, 5, 17, 10, 30))  # Sunday

    assert engine._is_non_trading_premarket_window()


def test_pid_liveness_check_detects_current_process():
    assert _is_pid_alive(paper_module.os.getpid())
    assert not _is_pid_alive("")
