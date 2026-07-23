"""Top-level paper/backtest/live engine orchestration."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime

from openalphastack.paths import DATA_DIR, add_legacy_paths
from openalphastack.engine.clock import TradingClock
from openalphastack.engine.constants import PRE_MARKET_START
from openalphastack.engine.data_feed import BacktestDataFeed
from openalphastack.engine.execution import ExecutionEngine
from openalphastack.engine.fast_lane import FastLane
from openalphastack.engine.ledger import Ledger
from openalphastack.engine.plan import PlanManager
from openalphastack.engine.state import EngineState
from openalphastack.engine.trading_calendar import is_trading_day, non_trading_reason
from openalphastack.engine.workflow_events import WorkflowEventStore

add_legacy_paths()
OUTPUT_BASE = str(DATA_DIR / "output")
os.makedirs(OUTPUT_BASE, exist_ok=True)

@contextmanager
def _prevent_windows_sleep(enabled: bool = True):
    """Keep Windows awake while a paper/live engine is expected to trade."""
    if not enabled or os.name != "nt":
        yield
        return
    try:
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_AWAYMODE_REQUIRED = 0x00000040
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        )
        yield
    finally:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass

try:
    from openalphastack.tools.notifier import (
        notify_alert,
        notify_backtest_complete,
        notify_backtest_progress,
        notify_engine_start,
        notify_non_trading_premarket,
        notify_trade,
        notify_trading_day_end,
    )
    _notify = True
except Exception:
    _notify = False

class PaperEngine:
    """Deterministic paper/backtest engine driven by externally published plans.

    Normal flow:
      before market - Codex publishes a validated plan through MCP
      9:15-15:00 - FastLane follows plan.json mechanically
      after close - Python-only report, no new plan
      emergency - hard rules pause execution and notify the operator
    """

    def __init__(self, mode: str = "paper", capital: float = 100000,
                 universe: list[str] = None,
                 backtest_start: str = None, backtest_end: str = None,
                 resume_run_id: str = None,
                 bar_period: int = 60):
        self.mode = mode
        self.bar_period = bar_period
        self.universe = universe or []
        self._stop_event = threading.Event()
        self._fast_lane_reset_date = None

        # Determine output directory
        if resume_run_id:
            self.run_id = resume_run_id
            self.output_dir = os.path.join(OUTPUT_BASE, resume_run_id)
        else:
            now_iso = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            self.run_id = f"{mode}_{now_iso}"
            self.output_dir = os.path.join(OUTPUT_BASE, self.run_id)

        os.makedirs(self.output_dir, exist_ok=True)

        # Initialize clock
        sim_start = None
        if mode == "backtest" and backtest_start:
            sim_start = datetime.strptime(backtest_start, "%Y-%m-%d")
        self.clock = TradingClock(mode, sim_start)

        # Initialize data feed
        self.data_feed = None
        if mode == "backtest" and universe and backtest_start and backtest_end:
            self.data_feed = BacktestDataFeed(
                backtest_start, backtest_end, universe, bar_period
            )

        # Initialize core components
        self.state = EngineState(self.output_dir, capital)
        self.plan = PlanManager(self.output_dir)
        self.ledger = Ledger(self.output_dir)
        self.workflow = WorkflowEventStore(self.output_dir, run_id=self.run_id)
        self.execution = ExecutionEngine(
            self.state,
            self.plan,
            self.ledger,
            mode,
            run_id=self.run_id,
            notify_trade_func=notify_trade if _notify else None,
        )
        self.fast_lane = FastLane(
            self.state, self.plan, self.execution,
            self.clock, mode,
            self.universe, self.data_feed,
            workflow=self.workflow,
        )
        # Persist engine metadata for /status display
        meta = {
            "mode": mode,
            "universe_size": len(self.universe) if self.universe else 0,
            "plan_source": "external_mcp",
            "process_id": os.getpid(),
            "started_at": datetime.now().isoformat(),
        }
        if mode == "backtest":
            meta["backtest_start"] = backtest_start or ""
            meta["backtest_end"] = backtest_end or ""
            meta["progress"] = {"current_day": 0, "total_days": 0}
        self.state.set_engine_meta(**meta)

    def _has_actionable_plan_for_today(self) -> bool:
        """Return whether paper/live can trade without generating a new plan."""
        if self.mode == "paper":
            self.plan.refresh_external()
        plan = self.plan.load()
        if plan.get("updated_by") == "init":
            return False

        today = self.clock.now().strftime("%Y-%m-%d")
        if plan.get("plan_date") != today:
            return False

        valid_candidates = [
            c for c in plan.get("buy_candidates", [])
            if c.get("valid_until", today) >= today
        ]
        return bool(
            valid_candidates
            or plan.get("holding_adjustments")
            or plan.get("bias_reasoning")
        )

    def _set_observation_mode(self, enabled: bool, reason: str = "") -> None:
        """Persist whether the engine is observing instead of actively trading."""
        self.state.set_engine_meta(
            status="observation" if enabled else "running",
            observation_mode=enabled,
            observation_reason=reason if enabled else "",
            stopped_at="",
        )

    def _heartbeat(self) -> None:
        """Persist current time so status views can reflect an alive idle engine."""
        self.state.set_data_time(self.clock.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.state.save()

    def _should_idle_for_calendar(self) -> bool:
        """Return whether the engine should stay in closed-market idle observation."""
        now = self.clock.now()
        if not is_trading_day(now.date()):
            return True
        phase = self.clock.session_phase()
        return phase == "closed"

    def _observation_reason_for_current_time(self) -> str:
        """Return a human-readable reason for the current observation state."""
        now = self.clock.now()
        if not is_trading_day(now.date()):
            return f"{self._non_trading_day_reason()}; waiting for next trading session"
        phase = self.clock.session_phase()
        if phase == "closed":
            return "交易日前等待盘前时段"
        if phase in {"auction", "morning", "afternoon"}:
            return "缺少 Codex 通过 MCP 发布的今日计划；引擎保持观察模式"
        if phase in {"post_market", "lunch", "pre_open"}:
            return f"{phase}; waiting for next actionable session"
        return "等待下一个可执行时段"

    def _reset_fast_lane_for_day_once(self, today) -> None:
        """Reset FastLane daily state once per calendar day."""
        if self._fast_lane_reset_date == today:
            return
        self.fast_lane.reset_day()
        self._fast_lane_reset_date = today

    def _is_non_trading_premarket_window(self) -> bool:
        """Return whether a closed-market day should emit its daily pre-market notice."""
        now = self.clock.now()
        if self.mode == "backtest":
            return False
        return now.time() >= PRE_MARKET_START and not is_trading_day(now.date())

    def _non_trading_day_reason(self) -> str:
        """Human-readable reason for skipping a closed-market day."""
        return non_trading_reason(self.clock.now().date())

    def _handle_non_trading_premarket(self) -> None:
        """Notify once during pre-market on a closed-market day, then skip actions."""
        now = self.clock.now()
        reason = self._non_trading_day_reason()
        observation_reason = (
            f"{reason}; skipping pre-market plan and intraday execution"
        )
        self._set_observation_mode(True, observation_reason)
        self._heartbeat()
        print(
            f"[Engine] Non-trading pre-market: {reason}. "
            "Skipping plan generation and intraday actions.",
            flush=True,
        )
        if _notify:
            notify_non_trading_premarket(
                self.run_id,
                now.strftime("%Y-%m-%d"),
                reason,
                self.state.total_value,
                len(self.state.holdings),
            )

    def run_post_close(self) -> dict:
        """Generate the deterministic Python-only post-close daily report.
        Saves JSON to daily_reports/YYYY-MM-DD.json.
        """
        import json as _json

        today_str = self.clock.now().strftime("%Y-%m-%d")
        nav = self.state.total_value
        cash = self.state.cash
        data = self.state._data
        initial = self.state.initial_capital

        # Compute day P&L from unrealized + realized changes
        positions = self.state.holdings
        day_pnl = sum(
            h.get("unrealized_pnl", 0) for h in positions.values()
        )

        report = {
            "date": today_str,
            "run_id": self.run_id,
            "mode": self.mode,
            "nav": round(nav, 2),
            "cash": round(cash, 2),
            "position_value": round(nav - cash, 2),
            "day_pnl": round(day_pnl, 2),
            "day_return_pct": round(day_pnl / max(initial, 1) * 100, 2),
            "total_return_pct": round((nav - initial) / initial * 100, 2),
            "holdings_count": len(positions),
            "trade_count": data.get("trade_count", 0),
            "win_count": data.get("win_count", 0),
            "win_rate": (
                round(data.get("win_count", 0) / max(data.get("trade_count", 0), 1) * 100, 1)
            ),
            "commission": data.get("total_commission", 0),
            "stamp_duty": data.get("total_stamp_duty", 0),
            "positions": {
                k: {
                    "shares": v.get("shares", 0),
                    "avg_cost": v.get("avg_cost", 0),
                    "current_price": v.get("current_price", 0),
                    "unrealized_pnl": v.get("unrealized_pnl", 0),
                    "strategy": v.get("strategy", ""),
                }
                for k, v in positions.items()
            },
            "market_bias": self.plan._data.get("market_bias", "neutral"),
            "bias_confidence": self.plan._data.get("bias_confidence", 0),
            "buy_candidates": len(self.plan._data.get("buy_candidates", [])),
        }

        reports_dir = os.path.join(self.output_dir, "daily_reports")
        os.makedirs(reports_dir, exist_ok=True)
        report_path = os.path.join(reports_dir, f"{today_str}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            _json.dump(report, f, ensure_ascii=False, indent=2)

        print(f"[PostClose] {today_str} NAV={nav:,.0f} P&L={day_pnl:+,.0f} "
              f"Trades={report['trade_count']} WinRate={report['win_rate']}% "
              f"Return={report['total_return_pct']:+.2f}%")
        self._record_workflow(
            "success",
            phase="postclose",
            node_id="daily_report",
            node_name="盘后日报",
            summary=f"盘后报告完成，成交 {report['trade_count']} 笔",
            output_refs=["review.daily_report"],
            output_payload=report,
        )
        return report

    def _record_workflow(self, status: str, **kwargs) -> None:
        """Record observability events without affecting engine execution."""
        try:
            if status == "running":
                self.workflow.record_node_start(**kwargs)
            elif status == "error":
                self.workflow.record_node_error(**kwargs)
            else:
                self.workflow.record_node_finish(**kwargs)
        except Exception as exc:
            print(f"[Workflow] record failed: {exc}", flush=True)

    def run_paper(self) -> None:
        """Run paper/live mode: pre-market plan, intraday execution, post-close report."""
        with _prevent_windows_sleep(self.mode in {"paper", "live"}):
            self._run_paper_loop()

    def _run_paper_loop(self) -> None:
        """Internal paper/live loop. Wrapped by run_paper for OS wake lock handling."""
        print(f"[Engine] Mode: {self.mode.upper()} | Run ID: {self.run_id}", flush=True)
        print(f"[Engine] Output: {self.output_dir}", flush=True)

        start_phase = self.clock.session_phase()
        if start_phase != "pre_market" and not self._has_actionable_plan_for_today():
            reason = self._observation_reason_for_current_time()
            self._set_observation_mode(True, reason)
            print(f"[Engine] Observation mode: {reason}", flush=True)
        else:
            self._set_observation_mode(False)

        if _notify:
            notify_engine_start(self.mode, self.state.initial_capital)

        _last_plan_date = None
        _post_market_date = None    # date() of last post-market summary
        _last_phase = start_phase
        tick_count = 0

        while not self._stop_event.is_set():
            phase = self.clock.session_phase()
            today = self.clock.now().date()
            should_idle = self._should_idle_for_calendar()
            if phase != _last_phase:
                print(
                    f"[Engine] Phase transition: {_last_phase} -> {phase} "
                    f"at {self.clock.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    flush=True,
                )
                _last_phase = phase

            # Closed-market days still get one pre-market Feishu notice.
            if self._is_non_trading_premarket_window() and _last_plan_date != today:
                self._handle_non_trading_premarket()
                _last_plan_date = today

            if should_idle:
                meta = self.state.load().get("engine_meta", {})
                reason = self._observation_reason_for_current_time()
                if (
                    not meta.get("observation_mode")
                    or meta.get("observation_reason") != reason
                ):
                    self._set_observation_mode(True, reason)
                self._heartbeat()
                time.sleep(1)
                continue

            # Post-market: Python-only daily summary and T+1 lock release. No plan generation here.
            if phase == "post_market" and _post_market_date != today:
                _post_market_date = today
                self.state.release_t1_locks()

                # Python-only post-close report
                report = self.run_post_close()

                if _notify:
                    notify_trading_day_end(
                        self.run_id, report["nav"], report["day_pnl"],
                        report["day_return_pct"],
                        self.state.holdings, report["trade_count"],
                    )

            # During trading hours, run FastLane
            if self.clock.is_trading():
                self._reset_fast_lane_for_day_once(today)
                if not self._has_actionable_plan_for_today():
                    meta = self.state.load().get("engine_meta", {})
                    reason = (
                        meta.get("observation_reason")
                        if meta.get("observation_mode")
                        else ""
                    )
                    self._set_observation_mode(
                        True,
                        reason or self._observation_reason_for_current_time(),
                    )
                    self._heartbeat()
                    time.sleep(1)
                    continue

                # Execute holding adjustments at auction/morning open
                if phase in ("auction", "morning"):
                    adjustments = self.fast_lane.execute_holding_adjustments()
                    if adjustments:
                        print(f"[Engine] Executed {len(adjustments)} holding adjustments")

                # Main tick loop
                result = self.fast_lane.tick()
                tick_count += 1
                events = result["events"]

                if events:
                    print(f"[Tick] {len(events)} events")

                # Emergency check
                if result["emergency"]:
                    print(f"[EMERGENCY] {result['trigger_reason']}")
                    if _notify:
                        notify_alert("critical", "紧急触发", result["trigger_reason"])

            self._heartbeat()
            time.sleep(1)

    def run_backtest(self) -> None:
        """Run in backtest mode: intraday bar-level historical replay.

        Bar period is set by --bar-period (default 60m: 4 bars/day).
        Stop-loss/take-profit checked every bar. Full signal scan every bar.
        The engine never invokes an Agent. Supply plans externally or use the
        deterministic signal path.
        """
        if not self.data_feed:
            print("[Engine] Error: backtest mode requires --start, --end, --universe")
            return

        trading_days = self.data_feed.trading_days()
        total_days = len(trading_days)
        # Count total bars for progress
        bar_counts = []
        for day in trading_days:
            bars = self.data_feed.get_day_bars(day)
            bar_counts.append(len(bars))

        total_bars = sum(bar_counts)
        period = self.bar_period
        print(f"[Engine] Backtest v4 | {total_days} days, ~{total_bars} bars "
              f"({period}m, {len(self.universe)} stocks) | deterministic")
        print(f"[Engine] Output: {self.output_dir}")

        if _notify:
            notify_engine_start(
                self.mode, self.state.initial_capital,
                start_date=trading_days[0].strftime("%Y-%m-%d") if total_days > 0 else "",
                end_date=trading_days[-1].strftime("%Y-%m-%d") if total_days > 0 else "",
            )

        t_start = time.time()
        # For 60m (4 bars/day) and coarser periods, scan every bar.
        # For finer periods, scan every bar too — the scan is cheap.
        SCAN_EVERY_N_BARS = 1

        for i, day in enumerate(trading_days):
            if self._stop_event.is_set():
                break

            day_str = day.strftime("%Y-%m-%d")

            # Clear previous day's stop-out list
            self.plan._data["today_stopped_out"] = []
            # Execute holding adjustments for this day
            self.fast_lane.reset_day()
            adjustments = self.fast_lane.execute_holding_adjustments()
            if adjustments:
                print(f"  [{day_str}] Adjustments: {len(adjustments)}")

            # Get bars for this day
            day_bars = self.data_feed.get_day_bars(day)

            if day_bars:
                # ── Bar-level intraday iteration ───────────
                day_events = []
                day_emergency = False
                day_emergency_reason = ""

                for j, bar_ts in enumerate(day_bars):
                    if self._stop_event.is_set():
                        break
                    self.clock.sim_time = bar_ts
                    self.state.set_data_time(bar_ts.strftime("%Y-%m-%d %H:%M:%S"))

                    do_full = (j % SCAN_EVERY_N_BARS == 0) or (j == len(day_bars) - 1)
                    result = self.fast_lane.tick(minute_ts=bar_ts, scan_signals=do_full)
                    day_events.extend(result["events"])
                    if result["emergency"]:
                        day_emergency = True
                        day_emergency_reason = result["trigger_reason"]

                if day_events:
                    print(f"  [{day_str}] Events: {len(day_events)}")
                if day_emergency:
                    print(f"  [{day_str}] EMERGENCY: {day_emergency_reason}")
            else:
                # Fallback: no intraday data, use daily close
                self.clock.sim_time = day.replace(hour=9, minute=30)
                self.state.set_data_time(day_str + " 09:30:00")
                result = self.fast_lane.tick()
                if result["events"]:
                    print(f"  [{day_str}] Events: {len(result['events'])}")
                if result["emergency"]:
                    print(f"  [{day_str}] EMERGENCY: {result['trigger_reason']}")

            # ── Post-close: Python-only report + T+1 release ──
            self.state.release_t1_locks()
            self.run_post_close()

            # Snapshot NAV once per day
            self.state.snapshot_nav()
            self.state.save()

            # ETA
            elapsed = time.time() - t_start
            pct_done = (i + 1) / total_days * 100
            if i > 0:
                eta_total = elapsed / (i + 1) * total_days
                eta_remaining = max(0, eta_total - elapsed)
                eta_str = f"{eta_remaining/60:.0f}min"
            else:
                eta_str = "..."

            print(f"[Backtest] {day_str} ({pct_done:.0f}%) | NAV: {self.state.total_value:,.0f} | "
                  f"Trades: {self.state._data['trade_count']} | Cash: {self.state.cash:,.0f} | "
                  f"ETA: {eta_str}")

            # Update progress in state.json for /status
            self.state.set_engine_meta(progress={"current_day": i + 1, "total_days": total_days})

            # Periodic progress notification
            if _notify and (i + 1) % 20 == 0:
                d = self.state._data
                t = d.get("trade_count", 0)
                w = d.get("win_count", 0)
                wr = w / t * 100 if t > 0 else 0
                notify_backtest_progress(i + 1, total_days, self.state.total_value, wr, t)

        # Backtest complete
        elapsed_total = time.time() - t_start
        print(f"[Backtest] Complete in {elapsed_total/60:.0f}min. "
              f"Final NAV: {self.state.total_value:,.0f}")

        # Final progress update
        self.state.set_engine_meta(progress={"current_day": total_days, "total_days": total_days})

        if _notify:
            data = self.state._data
            nav = self.state.total_value
            total_return = (nav - self.state.initial_capital) / self.state.initial_capital * 100
            trades = data.get("trade_count", 0)
            wins = data.get("win_count", 0)
            wr = wins / trades * 100 if trades > 0 else 0
            navs = [n["nav"] for n in data.get("nav_curve", [])]
            max_dd = 0.0
            if navs:
                peak = navs[0]
                for v in navs:
                    if v > peak:
                        peak = v
                    dd = (peak - v) / peak * 100 if peak > 0 else 0
                    if dd > max_dd:
                        max_dd = dd
            notify_backtest_complete(nav, total_return, wr, 0, max_dd, trades)

    def stop(self) -> None:
        """Signal the engine to stop gracefully."""
        self._stop_event.set()

    def stats(self) -> dict:
        """Return engine statistics."""
        s = self.state.load()
        p = self.plan.load()
        return {
            "mode": self.mode,
            "run_id": self.run_id,
            "initial_capital": s["initial_capital"],
            "total_value": self.state.total_value,
            "cash": s["cash"],
            "holdings_count": len(s["holdings"]),
            "trade_count": s.get("trade_count", 0),
            "win_count": s.get("win_count", 0),
            "market_bias": p.get("market_bias", "neutral"),
            "buy_candidates": len(p.get("buy_candidates", [])),
            "clock_time": self.clock.now().strftime("%Y-%m-%d %H:%M:%S"),
            "trading": self.clock.is_trading(),
        }


# ═══════════════════════════════════════════════════════════════
