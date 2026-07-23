from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from openalphastack.engine import paper as paper_module
from openalphastack.engine.paper import PaperEngine
from openalphastack.tools.engine_status import _is_pid_alive

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeClock:
    def __init__(self, now: datetime):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def session_phase(self) -> str:
        return "pre_market"

    def is_trading(self) -> bool:
        return False


class SequencedClock:
    def __init__(self, timestamps: list[datetime], phases: list[str], trading_flags: list[bool]):
        self._timestamps = timestamps
        self._phases = phases
        self._trading_flags = trading_flags
        self._index = 0

    def now(self) -> datetime:
        return self._timestamps[self._index]

    def session_phase(self) -> str:
        return self._phases[self._index]

    def is_trading(self) -> bool:
        return self._trading_flags[self._index]

    def advance(self) -> None:
        if self._index < len(self._timestamps) - 1:
            self._index += 1


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


def test_reasoning_only_plan_remains_observation_only(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.clock = FakeClock(datetime(2026, 5, 13, 8, 30))
    engine.plan.set_sim_now(datetime(2026, 5, 13, 8, 30))
    engine.plan.set_market_bias(
        "neutral",
        55,
        "盘前计划已生成，但今天不主动开仓。",
        position_cap=30,
    )
    engine.plan.mark_premarket_plan_generated(datetime(2026, 5, 13, 8, 30))

    assert not engine._has_actionable_plan_for_today()

    engine._set_observation_mode(True, "no executable candidates or holding adjustments")
    meta = engine.state.load()["engine_meta"]

    assert meta["observation_mode"] is True
    assert meta["observation_reason"] == "no executable candidates or holding adjustments"


def test_stale_plan_does_not_make_resumed_paper_run_actionable(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.plan.set_sim_now(datetime(2026, 5, 18, 8, 30))
    engine.plan.set_market_bias(
        "neutral",
        55,
        "历史盘前计划，恢复时不能直接盘中执行。",
        position_cap=30,
    )
    engine.clock = FakeClock(datetime(2026, 5, 19, 14, 0))

    assert not engine._has_actionable_plan_for_today()


def test_stale_plan_with_holdings_does_not_make_resumed_paper_run_actionable(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.state.set_data_time("2026-05-18 10:00:00")
    engine.state.add_holding("600036", 100, 40.0, "old_plan", stop_loss=38.0, take_profit=44.0)
    engine.plan.set_sim_now(datetime(2026, 5, 18, 8, 30))
    engine.plan.set_market_bias(
        "neutral",
        55,
        "历史盘前计划，恢复时不能因为仍有持仓就直接执行。",
        position_cap=30,
    )
    engine.clock = FakeClock(datetime(2026, 5, 19, 14, 0))

    assert not engine._has_actionable_plan_for_today()


def test_metadata_update_does_not_make_stale_plan_actionable(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.plan.set_sim_now(datetime(2026, 5, 18, 8, 30))
    engine.plan.set_market_bias(
        "neutral",
        55,
        "昨天的盘前计划，今天不能因为冷却写入变成可执行。",
        position_cap=30,
    )
    engine.clock = FakeClock(datetime(2026, 5, 19, 9, 42))
    engine.plan.set_sim_now(datetime(2026, 5, 19, 9, 42))
    engine.plan.mark_stopped_out("600036", cooldown_hours=24)

    plan = engine.plan.load()
    assert plan["updated"].startswith("2026-05-19")
    assert plan["updated_by"] == "stop_cooldown"
    assert not engine._has_actionable_plan_for_today()


def test_today_metadata_update_does_not_create_an_action(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.clock = FakeClock(datetime(2026, 5, 19, 8, 30))
    engine.plan.set_sim_now(datetime(2026, 5, 19, 8, 30))
    engine.plan.set_market_bias(
        "neutral",
        55,
        "今天的盘前计划，允许空仓观察。",
        position_cap=30,
    )
    engine.plan.mark_premarket_plan_generated(datetime(2026, 5, 19, 8, 30))
    engine.plan.set_sim_now(datetime(2026, 5, 19, 9, 42))
    engine.plan.mark_stopped_out("600036", cooldown_hours=24)

    assert engine.plan.load()["updated_by"] == "stop_cooldown"
    assert not engine._has_actionable_plan_for_today()


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


def test_run_paper_stays_alive_in_closed_market_observation_until_stopped(output_base, monkeypatch):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    engine.clock = SequencedClock(
        [datetime(2026, 5, 17, 10, 30), datetime(2026, 5, 17, 10, 30)],
        ["weekend", "weekend"],
        [False, False],
    )
    sleep_calls = []

    def fake_sleep(_seconds: int) -> None:
        sleep_calls.append("sleep")
        engine.stop()

    monkeypatch.setattr(paper_module.time, "sleep", fake_sleep)

    engine.run_paper()

    meta = engine.state.load()["engine_meta"]
    assert sleep_calls == ["sleep"]
    assert meta["status"] == "observation"
    assert meta["observation_mode"] is True
    assert "waiting for next trading session" in meta["observation_reason"]


def test_run_paper_wraps_loop_with_windows_sleep_guard(output_base, monkeypatch):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    calls = []

    class FakeSleepGuard:
        def __init__(self, enabled: bool):
            calls.append(("guard", enabled))

        def __enter__(self):
            calls.append(("enter", None))

        def __exit__(self, exc_type, exc, tb):
            calls.append(("exit", None))

    monkeypatch.setattr(paper_module, "_prevent_windows_sleep", FakeSleepGuard)
    monkeypatch.setattr(engine, "_run_paper_loop", lambda: calls.append(("loop", None)))

    engine.run_paper()

    assert calls == [
        ("guard", True),
        ("enter", None),
        ("loop", None),
        ("exit", None),
    ]


def test_fast_lane_reset_runs_once_per_trading_day(output_base):
    engine = PaperEngine(mode="paper", capital=100000, universe=["600036"])
    calls = []
    engine.fast_lane = SimpleNamespace(reset_day=lambda: calls.append("reset"))

    engine._reset_fast_lane_for_day_once(datetime(2026, 5, 18).date())
    engine._reset_fast_lane_for_day_once(datetime(2026, 5, 18).date())
    engine._reset_fast_lane_for_day_once(datetime(2026, 5, 19).date())

    assert calls == ["reset", "reset"]


def test_pid_liveness_check_detects_current_process():
    assert _is_pid_alive(paper_module.os.getpid())
    assert not _is_pid_alive("")
