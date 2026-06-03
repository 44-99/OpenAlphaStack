"""Fast-lane intraday execution logic."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

from alphaclaude.paths import add_legacy_paths
from alphaclaude.engine.clock import TradingClock
from alphaclaude.engine.data_feed import BacktestDataFeed
from alphaclaude.engine.execution import ExecutionEngine
from alphaclaude.engine.plan import PlanManager
from alphaclaude.engine.state import EngineState, round_lot
from alphaclaude.engine.t0 import T0Tracker

add_legacy_paths()

class FastLane:
    """Python fast lane: price monitoring, stop/profit, buy candidates, rule signals, emergency detection.

    Zero LLM calls in normal operation. Only detects emergency conditions for OvernightPipeline to handle.
    """

    # ── Volatility filter ──────────────────────────────────────────

    def _get_avg_daily_range_pct(self, code: str) -> float | None:
        """Return 10-day average daily range (high-low)/close * 100.

        Used to filter out low-volatility stocks that can't generate
        meaningful returns within the strategy's holding period.
        Returns None if insufficient data.
        """
        if self.data_feed and self.mode == "backtest":
            try:
                now = self.clock.now()
                hist = self.data_feed.get_history_up_to(
                    code, pd.Timestamp(now.strftime("%Y-%m-%d")), days=15)
                if hist is not None and len(hist) >= 5:
                    ranges = [
                        (float(r["high"]) - float(r["low"])) / float(r["close"]) * 100
                        for _, r in hist.iterrows() if float(r["close"]) > 0
                    ]
                    if ranges:
                        return sum(ranges) / len(ranges)
            except Exception:
                pass
        return None

    # ── Signal detail builder ────────────────────────────────────

    @staticmethod
    def _build_signal_detail(sig: dict) -> str:
        """Build a human-readable signal detail string from a rule signal dict."""
        rule = sig.get("rule", "")
        parts = []
        if rule in ("ma_golden_cross", "ma_death_cross"):
            parts.append(f"MA5={sig.get('ma5','?')} MA10={sig.get('ma10','?')}")
        elif rule == "volume_breakout":
            parts.append(f"量比={sig.get('vol_ratio','?')} 涨幅={sig.get('price_change_pct','?')}%")
        elif rule == "deviation_alert":
            parts.append(f"乖离={sig.get('deviation_pct','?')}% MA5={sig.get('ma5','?')}")
        elif rule in ("alignment_turn_bullish", "alignment_turn_bearish"):
            parts.append(
                f"MA5={sig.get('ma5','?')} MA10={sig.get('ma10','?')} MA20={sig.get('ma20','?')}"
            )
        elif rule in ("gap_up", "gap_down"):
            parts.append(f"缺口={sig.get('gap_pct','?')}% 昨收={sig.get('prev_close','?')}")
        elif rule == "volume_spike":
            parts.append(f"量比={sig.get('vol_ratio','?')} 均量={sig.get('avg_volume_5d','?')}")
        if sig.get("suggested_stop"):
            parts.append(f"建议止损={sig['suggested_stop']}")
        if sig.get("confidence"):
            parts.append(f"置信度={sig['confidence']}")
        return " | ".join(parts)

    # ── Init ─────────────────────────────────────────────────────

    def __init__(self, state: EngineState, plan: PlanManager,
                 execution: ExecutionEngine, clock: TradingClock, mode: str,
                 universe: list[str], data_feed: BacktestDataFeed = None,
                 workflow=None):
        self.state = state
        self.plan = plan
        self.execution = execution
        self.clock = clock
        self.mode = mode
        self.universe = universe or []
        self.data_feed = data_feed
        self.workflow = workflow
        # Monitored = holdings + buy candidates only.
        # The full universe is Claude Code's selection pool (via screen.py), not scanned every tick.
        self._monitored = set()
        self._prev_market_price = 0.0
        self._adjustments_executed = False
        self._signal_history: dict[str, set] = {}
        self._last_reset_day = None
        self._circuit_breaker_triggered = False
        self._circuit_breaker_reason = ""
        # Tiered emergency dedup: code → highest tier already fired (1/2/3)
        # Market/account use key "market"/"account" with tier 0
        self._emergency_tiers: dict[str, int] = self.plan.get_emergency_tiers()
        # T+0 intraday tracking
        self._t0_trackers: dict[str, "T0Tracker"] = {}   # code → tracker
        self._t0_global_pause_until = ""                  # HH:MM, global pause after spike
        self._t0_total_pnl = 0.0                          # daily cumulative T+0 P&L
        self._candidate_rejections: set[tuple[str, str, str]] = set()
        self._daily_new_positions = self._count_existing_daily_new_positions()

    def _count_existing_daily_new_positions(self) -> int:
        """Best-effort count of today's already-opened positions for cap enforcement."""
        read_all = getattr(self.execution.ledger, "read_all", None)
        if not callable(read_all):
            return 0
        try:
            entries = read_all()
        except Exception:
            return 0
        if not isinstance(entries, list):
            return 0
        today = self.clock.now().strftime("%Y%m%d")
        count = 0
        for entry in entries:
            if entry.get("decision") != "open_position":
                continue
            trade_id = str(entry.get("trade_id") or "")
            entry_date = str(entry.get("date") or "").replace("-", "")
            if trade_id.startswith(today) or entry_date.startswith(today):
                count += 1
        return count

    def _daily_new_position_limit(self) -> int:
        rules = self.plan.load().get("rules", {})
        try:
            return max(0, int(rules.get("daily_new_positions_limit", 3)))
        except (TypeError, ValueError):
            return 3

    def _reject_candidate(self, events: list, code: str, rule: str,
                          reason: str, strategy_type: str = "") -> None:
        today = self.clock.now().strftime("%Y-%m-%d")
        key = (today, code, rule)
        if key in self._candidate_rejections:
            return
        self._candidate_rejections.add(key)
        self.execution.ledger.append({
            "decision": "rejected_buy",
            "symbol": code,
            "rule": rule,
            "reason": reason,
            "strategy_type": strategy_type,
        })
        events.append({
            "event": "candidate_rejected",
            "code": code,
            "rule": rule,
            "reason": reason,
            "strategy_type": strategy_type,
        })

    @staticmethod
    def _time_reached(now: datetime, hhmm: str) -> bool:
        try:
            hour, minute = [int(part) for part in str(hhmm).split(":", 1)]
        except (TypeError, ValueError):
            hour, minute = 9, 45
        return now.time() >= now.replace(hour=hour, minute=minute, second=0, microsecond=0).time()

    def _candidate_blocked(self, candidate: dict, now: datetime, events: list) -> bool:
        code = candidate.get("code", "")
        strategy_type = candidate.get("strategy_type", "watch_only")
        if strategy_type == "watch_only":
            self._reject_candidate(
                events,
                code,
                "watch_only",
                "候选被标记为 watch_only，不自动买入",
                strategy_type,
            )
            return True
        if strategy_type == "breakout" and not self._time_reached(now, candidate.get("confirm_after", "09:45")):
            return True
        if self._daily_new_positions >= self._daily_new_position_limit():
            self._reject_candidate(
                events,
                code,
                "daily_new_positions_limit",
                f"达到单日新开仓上限 {self._daily_new_position_limit()}",
                strategy_type,
            )
            return True
        return False

    def _check_circuit_breaker(self) -> tuple[bool, str]:
        """Global circuit breaker: halt new positions at -20% drawdown."""
        if self.state.initial_capital <= 0:
            return False, ""
        drawdown = (self.state.total_value - self.state.initial_capital) / self.state.initial_capital * 100
        if drawdown <= -20:
            return True, f"熔断: 账户回撤{drawdown:.1f}% (限额-20%)"
        return False, ""

    def _load_t0_configs(self) -> None:
        """Load T+0 configurations from plan holding_adjustments at day start.

        Preserves active T+0 positions across days. Falls back to auto-generating
        default T+0 configs from buy_candidates when plan has no explicit t0_config.
        """
        # Preserve active T+0 positions before clearing
        active_trackers = {
            code: t for code, t in self._t0_trackers.items()
            if t.state in ("active_buy", "active_sell")
        }
        self._t0_trackers.clear()
        self._t0_global_pause_until = ""
        self._t0_total_pnl = 0.0

        # Primary: load explicit t0_config from holding_adjustments
        found_any = False
        for adj in self.plan.get_holding_adjustments():
            code = adj.get("code", "")
            t0_cfg = adj.get("t0_config")
            if not t0_cfg or not code:
                continue
            h = self.state.holdings.get(code, {})
            available = h.get("available", h.get("shares", 0))
            if available < 200:
                continue
            tracker = T0Tracker(code)
            tracker.load_config(t0_cfg, available)
            if tracker.enabled and tracker.max_shares >= 100:
                self._t0_trackers[code] = tracker
                found_any = True

        if not found_any:
            # Fallback: auto-generate T+0 configs from buy_candidates for current holdings
            for code, h in self.state.holdings.items():
                available = h.get("available", h.get("shares", 0))
                if available < 200:
                    continue
                c = self.plan.get_candidate(code)
                if not c:
                    continue
                priority = c.get("priority", 2)
                avg_cost = h.get("avg_cost", 0)
                if avg_cost <= 0:
                    continue

                # Default parameters per bucket (from t+0-intraday skill)
                if priority == 1:  # Core: forward T preferred, wider params
                    direction = "forward"
                    max_shares_pct = 30
                    sell_target_pct = 2.0
                    stop_loss_pct = -1.5
                    max_rounds = 2
                elif priority == 2:  # Satellite
                    direction = "both"
                    max_shares_pct = 25
                    sell_target_pct = 1.5
                    stop_loss_pct = -1.0
                    max_rounds = 1
                else:  # Scout: skip T+0 (position too small)
                    continue

                buy_trigger = round(avg_cost * 0.995, 2)
                breakout = round(avg_cost * 1.05, 2)
                breakdown = round(avg_cost * 0.95, 2)

                tracker = T0Tracker(code)
                tracker.load_config({
                    "enabled": True,
                    "preferred_direction": direction,
                    "max_shares_pct": max_shares_pct,
                    "buy_trigger_price": buy_trigger,
                    "sell_target_pct": sell_target_pct,
                    "stop_loss_pct": stop_loss_pct,
                    "max_rounds": max_rounds,
                    "breakout_price": breakout,
                    "breakdown_price": breakdown,
                    "atr_pct": 3.0,
                }, available)
                if tracker.max_shares >= 100:
                    self._t0_trackers[code] = tracker

        # Restore active T+0 positions — always runs, even after primary path
        for code, t in active_trackers.items():
            if code in self.state.holdings:
                t.rounds_done = 0  # Reset daily counter
                self._t0_trackers[code] = t  # Overwrite with active tracker

    # ── T+0 intraday execution ──────────────────────────────────

    def _check_t0_abort(self, tracker: "T0Tracker", price: float, vol_ratio: float,
                        now_time: str) -> str | None:
        """Check abort/breakout conditions. Returns reason if T+0 should abort, else None."""
        # Spike: 1-min change check via breakout/breakdown prices as proxy
        if tracker.breakout_price > 0 and price >= tracker.breakout_price:
            return "breakout_up"
        if tracker.breakdown_price > 0 and price <= tracker.breakdown_price:
            return "breakdown"
        # Volume anomaly: pause new rounds
        if vol_ratio > 5.0 and tracker.state == "idle":
            return "volume_spike"
        return None

    def _run_t0_cycle(self, quotes: dict, now_str: str, events: list) -> None:
        """T+0 intraday execution for all tracked holdings.

        Called each tick. Handles: abort checks → active position exits → idle entry triggers.
        """
        # Lazy-load T+0 configs for holdings that appear mid-day.
        # Only consider positions eligible for T+0 (Core/Satellite, >=200 shares).
        if self.state.holdings:
            tracked = set(self._t0_trackers.keys())
            t0_eligible = {
                code for code, h in self.state.holdings.items()
                if h.get("available", h.get("shares", 0)) >= 200
                and (c := self.plan.get_candidate(code))
                and c.get("priority", 3) <= 2  # Core or Satellite only
            }
            if t0_eligible - tracked:
                self._load_t0_configs()

        now_parts = now_str.split(" ")[-1] if " " in now_str else now_str
        now_time = now_parts[:5] if len(now_parts) >= 5 else now_parts

        # Skip during restricted periods (9:30-10:00, 14:30-15:00)
        # In backtest mode, skip time restrictions — we simulate full day via OHLC
        is_bt = (self.mode == "backtest" and self.data_feed is not None)
        if not is_bt:
            if "09:30" <= now_time < "10:00":
                return
            if now_time >= "14:30":
                return

        # Global pause (after spike, 5 minutes)
        if self._t0_global_pause_until and now_time < self._t0_global_pause_until:
            return

        # Daily T+0 loss cap
        if self._t0_total_pnl < -(self.state.initial_capital * 0.005):
            return  # Stop all T+0 for today

        for code, tracker in list(self._t0_trackers.items()):
            if not tracker.enabled:
                continue

            h = self.state.holdings.get(code)
            if not h:
                self._t0_trackers.pop(code, None)
                continue

            q = quotes.get(code, {})
            price = q.get("price", h.get("current_price", 0))
            if price <= 0:
                continue

            available = h.get("available", h.get("shares", 0))
            vol_ratio = q.get("volume_ratio", 1.0)

            # Use point-in-time price only (no OHLC range even in backtest —
            # using day_high/day_low overstates execution quality).
            # Override is_bt for trigger logic: always use paper-mode price-based checks.
            is_bt = False

            # ── Abort check ──────────────────────────────────
            abort_reason = self._check_t0_abort(tracker, price, vol_ratio, now_time)
            if abort_reason:
                if tracker.state == "active_buy":
                    if tracker.t0_shares >= 100:
                        exit_price = price
                        self.execution.execute_sell(
                            code, tracker.t0_shares, exit_price,
                            reason=f"T0_abort: {abort_reason}",
                        )
                        events.append({"event": "t0_abort", "code": code,
                                       "reason": abort_reason, "shares": tracker.t0_shares})
                    tracker.reset_day()
                    tracker.enabled = True
                    tracker.load_config(
                        self._get_t0_config_from_plan(code) or {}, available)
                elif tracker.state == "active_sell":
                    buy_price = price
                    buy_shares = min(tracker.t0_shares, round_lot(
                        int(self.state.cash * 0.9 / buy_price)))
                    if buy_shares >= 100:
                        r = self.execution.execute_buy(
                            code, buy_shares, buy_price,
                            strategy="t0_reverse",
                            reasoning=f"T0_abort_buyback: {abort_reason}",
                        )
                        if r.get("status") == "executed":
                            events.append({"event": "t0_abort_buyback", "code": code,
                                           "reason": abort_reason, "shares": buy_shares})
                    tracker.reset_day()

                if abort_reason in ("breakout_up", "breakdown"):
                    tracker.enabled = False
                continue

            # ── Pause check ──────────────────────────────────
            if tracker.paused_until and now_time < tracker.paused_until:
                continue

            # ── Active T+0 position: check exit ──────────────
            if tracker.state == "active_buy":
                sellable = min(tracker.t0_shares, available)
                if sellable < 100:
                    continue
                pnl_pct = (price - tracker.t0_entry_price) / tracker.t0_entry_price * 100
                target_hit = pnl_pct >= tracker.sell_target_pct
                stop_hit = pnl_pct <= tracker.stop_loss_pct

                if target_hit:
                    exit_price = price
                    r = self.execution.execute_sell(
                        code, sellable, exit_price,
                        reason=f"T0_target: {tracker.sell_target_pct}%",
                    )
                    if r and r.get("shares", 0) > 0:
                        t0_pnl = (exit_price - tracker.t0_entry_price) * sellable
                        self._t0_total_pnl += t0_pnl
                        tracker.rounds_done += 1
                        events.append({
                            "event": "t0_complete", "code": code,
                            "direction": "forward", "pnl": round(t0_pnl, 2),
                            "pnl_pct": round(tracker.sell_target_pct, 2), "round": tracker.rounds_done,
                        })
                        tracker.state = "idle"
                        tracker.t0_shares = 0
                        tracker.t0_entry_price = 0.0
                        tracker.t0_target_price = 0.0
                        tracker.t0_stop_price = 0.0
                elif stop_hit:
                    exit_price = price
                    r = self.execution.execute_sell(
                        code, sellable, exit_price,
                        reason=f"T0_stop: {tracker.stop_loss_pct}%",
                    )
                    if r and r.get("shares", 0) > 0:
                        t0_pnl = (exit_price - tracker.t0_entry_price) * sellable
                        self._t0_total_pnl += t0_pnl
                        tracker.rounds_done += 1
                        events.append({
                            "event": "t0_stopped", "code": code,
                            "direction": "forward", "pnl": round(t0_pnl, 2),
                            "pnl_pct": round(tracker.stop_loss_pct, 2), "round": tracker.rounds_done,
                        })
                        tracker.state = "idle"
                        tracker.t0_shares = 0
                        tracker.t0_entry_price = 0.0
                        tracker.t0_target_price = 0.0
                        tracker.t0_stop_price = 0.0
            elif tracker.state == "active_sell":
                pnl_pct = (tracker.t0_entry_price - price) / tracker.t0_entry_price * 100
                target_hit = pnl_pct >= tracker.sell_target_pct
                stop_hit = pnl_pct <= tracker.stop_loss_pct

                if target_hit:
                    buy_price = price
                    buy_shares = min(tracker.t0_shares, round_lot(
                        int(self.state.cash * 0.4 / buy_price)))
                    if buy_shares >= 100:
                        r = self.execution.execute_buy(
                            code, buy_shares, buy_price,
                            strategy="t0_reverse",
                            reasoning=f"T0_buyback: +{tracker.sell_target_pct}%",
                        )
                        if r.get("status") == "executed":
                            t0_pnl = (tracker.t0_entry_price - buy_price) * buy_shares
                            self._t0_total_pnl += t0_pnl
                            tracker.rounds_done += 1
                            events.append({
                                "event": "t0_complete", "code": code,
                                "direction": "reverse", "pnl": round(t0_pnl, 2),
                                "pnl_pct": round(tracker.sell_target_pct, 2), "round": tracker.rounds_done,
                            })
                            tracker.state = "idle"
                            tracker.t0_shares = 0
                            tracker.t0_entry_price = 0.0
                            tracker.t0_target_price = 0.0
                            tracker.t0_stop_price = 0.0
                elif stop_hit:
                    buy_price = tracker.t0_stop_price if is_bt else price
                    if is_bt and buy_price <= 0:
                        buy_price = price
                    buy_shares = min(tracker.t0_shares, round_lot(
                        int(self.state.cash * 0.4 / buy_price)))
                    if buy_shares >= 100:
                        r = self.execution.execute_buy(
                            code, buy_shares, buy_price,
                            strategy="t0_reverse",
                            reasoning=f"T0_stop_buyback: {tracker.stop_loss_pct}%",
                        )
                        if r.get("status") == "executed":
                            t0_pnl = (tracker.t0_entry_price - buy_price) * buy_shares
                            self._t0_total_pnl += t0_pnl
                            tracker.rounds_done += 1
                            events.append({
                                "event": "t0_stopped", "code": code,
                                "direction": "reverse", "pnl": round(t0_pnl, 2),
                                "pnl_pct": round(tracker.stop_loss_pct, 2), "round": tracker.rounds_done,
                            })
                            tracker.state = "idle"
                            tracker.t0_shares = 0
                            tracker.t0_entry_price = 0.0
                            tracker.t0_target_price = 0.0
                            tracker.t0_stop_price = 0.0

            # ── Idle: check entry triggers ────────────────────
            if tracker.state != "idle":
                continue
            if tracker.rounds_done >= tracker.max_rounds:
                continue
            if available < tracker.max_shares:
                continue

            # Skip T+0 for low-volatility stocks — costs exceed expected profit
            if tracker.atr_pct < 2.0:
                continue

            direction = tracker.preferred_direction

            if direction in ("forward", "both"):
                if tracker.buy_trigger_price > 0:
                    # Live/paper: price within 0.5% of trigger
                    trigger_fired = (tracker.buy_trigger_price * 0.995 <= price <=
                                     tracker.buy_trigger_price * 1.005)
                    entry_price = price

                    if trigger_fired:
                        t0_shares = tracker.max_shares
                        estimated_cost = entry_price * t0_shares * 1.001
                        if estimated_cost > self.state.cash * 0.4:
                            t0_shares = round_lot(int(self.state.cash * 0.4 / entry_price / 100) * 100)
                        if t0_shares < 100:
                            continue
                        r = self.execution.execute_buy(
                            code, t0_shares, entry_price,
                            strategy="t0_forward",
                            reasoning=f"T0_forward: 回踩支撑 {tracker.buy_trigger_price}",
                        )
                        if r.get("status") == "executed":
                            tracker.state = "active_buy"
                            tracker.t0_shares = t0_shares
                            tracker.t0_entry_price = entry_price
                            tracker.t0_target_price = round(entry_price * (1 + tracker.sell_target_pct / 100), 2)
                            tracker.t0_stop_price = round(entry_price * (1 + tracker.stop_loss_pct / 100), 2)
                            events.append({
                                "event": "t0_entry", "code": code,
                                "direction": "forward", "shares": t0_shares,
                                "price": entry_price, "target": tracker.t0_target_price,
                                "stop": tracker.t0_stop_price,
                            })
                            continue

            if direction in ("reverse", "both"):
                if tracker.buy_trigger_price > 0:
                    sell_trigger_price = price * (1 + tracker.sell_target_pct / 100)
                    trigger_fired = price >= sell_trigger_price * 0.98
                    entry_price = price

                    if trigger_fired > 0 and trigger_fired:
                        t0_sell_shares = min(tracker.max_shares, available)
                        if t0_sell_shares < 100:
                            continue
                        r = self.execution.execute_sell(
                            code, t0_sell_shares, entry_price,
                            reason=f"T0_reverse_sell: 冲高 {entry_price}",
                        )
                        if r and r.get("shares", 0) > 0:
                            tracker.state = "active_sell"
                            tracker.t0_shares = t0_sell_shares
                            tracker.t0_entry_price = entry_price
                            target_drop = entry_price * (1 - tracker.sell_target_pct / 100)
                            tracker.t0_target_price = round(target_drop, 2)
                            tracker.t0_stop_price = round(entry_price * (1 - tracker.stop_loss_pct / 100), 2)
                            events.append({
                                "event": "t0_entry", "code": code,
                                "direction": "reverse", "shares": t0_sell_shares,
                                "price": entry_price, "buyback_target": tracker.t0_target_price,
                                "stop": tracker.t0_stop_price,
                            })

    def _get_t0_config_from_plan(self, code: str) -> dict | None:
        """Re-read t0_config for a specific code from current plan."""
        for adj in self.plan.get_holding_adjustments():
            if adj.get("code") == code:
                return adj.get("t0_config")
        return None

    def tick(self, minute_ts: pd.Timestamp | None = None,
             scan_signals: bool = True) -> dict:
        """One evaluation cycle. Parallel quotes + scans, action routing, dedup.

        minute_ts: 5-min bar timestamp for backtest minute-level iteration.
                   When None, falls back to daily close data (legacy / paper mode).
        scan_signals: when False, only stop-loss/take-profit checked (throttle mode).

        Returns {'events': [], 'emergency': bool, 'trigger_reason': str}.
        """
        events = []
        now = self.clock.now()
        self.plan.set_sim_now(now)  # keep cooldown/candidate expiry in sync
        emergency = False
        trigger_reason = ""

        # ── Circuit breaker check ─────────────────────────────
        if not self._circuit_breaker_triggered:
            triggered, reason = self._check_circuit_breaker()
            if triggered:
                self._circuit_breaker_triggered = True
                self._circuit_breaker_reason = reason
                events.append({
                    "event": "circuit_breaker_triggered",
                    "reason": reason,
                    "nav": self.state.total_value,
                })
                print(f"[CIRCUIT BREAKER] {reason}")

        # ── Monitored codes: holdings + buy candidates from plan ──
        codes = set(self.state.holdings.keys())
        for c in self.plan.get_buy_candidates():
            code = c.get("code", "")
            if code:
                codes.add(code)

        codes = [c for c in codes if c]
        if not codes:
            return {"events": events, "emergency": False, "trigger_reason": ""}

        # ── Quote fetch ───────────────────────────────────────
        # Mirrors paper mode: per-stock on-demand, only for monitored codes.
        quotes = {}
        if self.data_feed and self.mode == "backtest":
            if minute_ts is not None:
                # Minute-level: fetch per-code, exactly like paper mode's get_quote() loop
                for code in codes:
                    q = self.data_feed.get_minute_quote(code, minute_ts)
                    if q:
                        quotes[code] = q
            else:
                quotes = self.data_feed.current_day_data(
                    pd.Timestamp(now.strftime("%Y-%m-%d"))
                )
            # Add index data (lazy-loaded on first access)
            idx_q = self.data_feed.get_index_quote(pd.Timestamp(now.strftime("%Y-%m-%d")))
            if idx_q:
                quotes["000001"] = idx_q
        else:
            def _fetch_one(code):
                try:
                    from alphaclaude.tools._fallback import get_quote
                    q, _ = get_quote(code)
                    return code, q if not q.get("error") else {}
                except Exception:
                    return code, {}
            with ThreadPoolExecutor(max_workers=min(len(codes), 10)) as ex:
                futures = {ex.submit(_fetch_one, c): c for c in codes}
                for f in as_completed(futures):
                    code, q = f.result()
                    if q:
                        quotes[code] = q

            # Fetch market index for emergency detection (live/paper mode)
            try:
                from alphaclaude.tools.quote import get_market_overview
                overview = get_market_overview()
                if overview and not overview.get("error"):
                    for idx in overview.get("indices", []):
                        if "上证" in idx.get("name", ""):
                            quotes["000001"] = {
                                "price": idx.get("price", 0),
                                "change_pct": idx.get("change_pct", 0),
                            }
                            break
            except Exception:
                pass

        # Update prices in state
        for code, q in quotes.items():
            if q.get("code") == "000001":
                continue  # skip index in state update
            self.state.update_quote(code, q.get("price", 0))

        # Track market price for emergency comparison
        market_q = quotes.get("000001", {})
        current_market_price = market_q.get("price", 0)
        if current_market_price > 0 and self._prev_market_price <= 0:
            self._prev_market_price = current_market_price

        # 1. Stop-loss / take-profit triggers (always, even during circuit breaker)
        triggers = self.execution.check_stop_triggers(quotes)
        for t in triggers:
            events.append(t)

        # ── Throttle guard: skip signal-heavy sections in fast-tick mode ──
        if not scan_signals:
            if self.mode == "backtest":
                self.state.set_data_time(now.strftime("%Y-%m-%d %H:%M:%S"))
            self.state.save()
            return {"events": events, "emergency": False, "trigger_reason": ""}

        # 1.5 Time+condition auto-close — only close if held too long AND clearly losing
        # Core (p1): no time limit (max_hold_condition=None) — let winners run
        # Satellite (p2): close if held >= N days AND P&L < -1.0% (only clear losers)
        # Scout (p3): close if held >= N days AND P&L < -2.0% (small pos, more tolerance)
        # Rationale: previous thresholds (+1.0%/+0.5%) killed slightly-positive
        # positions. Low-vol stocks (banks) can't deliver >1% in 5 days.
        today_str = now.strftime("%Y-%m-%d")
        bucket_hold_condition = {1: None, 2: -1.0, 3: -2.0}
        for code, h in list(self.state.holdings.items()):
            entry_date = h.get("entry_date", "")
            available = h.get("available", h.get("shares", 0))
            if available <= 0:
                continue
            c = self.plan.get_candidate(code) or {}
            priority = c.get("priority", 2)
            min_pnl_pct = bucket_hold_condition.get(priority)
            if min_pnl_pct is None:
                continue  # Core position: no time limit, let it run
            max_days = c.get("max_hold_days", 5)
            try:
                held_days = (datetime.strptime(today_str, "%Y-%m-%d") -
                             datetime.strptime(entry_date, "%Y-%m-%d")).days
            except (ValueError, TypeError):
                held_days = 0
            if held_days < max_days:
                continue
            # Check P&L
            avg_cost = h.get("avg_cost", 0)
            q = quotes.get(code, {})
            price = q.get("price", h.get("current_price", 0))
            if avg_cost > 0 and price > 0:
                pnl_pct = (price - avg_cost) / avg_cost * 100
                if pnl_pct >= min_pnl_pct:
                    continue  # Performing well, keep holding
            self.execution.execute_sell(
                code, available, price,
                reason=f"持仓到期且无盈利: 持有{held_days}天>={max_days}天 P&L={pnl_pct:.1f}%<{min_pnl_pct}%",
            )
            self.plan.mark_stopped_out(code, cooldown_hours=72)  # 3-day cooldown to prevent churning
            events.append({"event": "max_hold_close", "code": code,
                           "price": price, "held_days": held_days, "pnl_pct": round(pnl_pct, 2)})

        # 1.6 T+0 intraday cycle — execute active positions, check entry triggers
        self._run_t0_cycle(quotes, now.strftime("%Y-%m-%d %H:%M:%S"), events)

        # ── If circuit breaker active, skip all new buys ──────
        if self._circuit_breaker_triggered:
            # Still do emergency detection and NAV snapshot
            emergency, trigger_reason = self._check_emergency(quotes)
            if self.mode == "backtest":
                self.state.set_data_time(now.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                self.state.set_data_time(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            if now.strftime("%H:%M:%S") == "15:00:00":
                self.state.snapshot_nav()
            self.state.save()
            return {"events": events, "emergency": emergency, "trigger_reason": trigger_reason}

        # 2. Buy candidate check — bucket-based allocation
        # Core (priority=1): up to 50%, Satellite (priority=2): up to 30%
        # Scout (priority=3): up to 20%, Emergency reserve: 20% min cash
        self.plan.clear_expired_cooldowns()
        stopped_today = self.plan.get_stopped_out_today()
        candidates = self.plan.get_buy_candidates()
        if candidates:
            total_value = self.state.total_value
            cash = self.state.cash
            _ = total_value - cash

            bucket_config = {
                1: {"name": "core", "cap_pct": 50, "default_stop_pct": -8, "max_hold_condition": None},
                2: {"name": "satellite", "cap_pct": 30, "default_stop_pct": -5, "max_hold_condition": 1.0},
                3: {"name": "scout", "cap_pct": 20, "default_stop_pct": -3, "max_hold_condition": 0.5},
            }

            for priority in (1, 2, 3):
                cfg = bucket_config[priority]
                bucket_cap_value = total_value * (cfg["cap_pct"] / 100.0)
                # Current value already in this bucket
                bucket_used = sum(
                    h["shares"] * h.get("current_price", h.get("avg_cost", 0))
                    for code, h in self.state.holdings.items()
                    if self.plan.get_candidate(code) and self.plan.get_candidate(code).get("priority") == priority
                )
                bucket_remaining = max(0, bucket_cap_value - bucket_used)

                bucket_candidates = [c for c in candidates if c.get("priority") == priority]
                for c in bucket_candidates:
                    code = c.get("code", "")
                    strategy_type = c.get("strategy_type", "watch_only")
                    if self._candidate_blocked(c, now, events):
                        continue
                    if self.plan.is_on_cooldown(code):
                        continue
                    if code in stopped_today:
                        continue
                    if code in self.state.holdings:
                        continue

                    q = quotes.get(code, {})
                    price = q.get("price", 0)
                    bar_high = q.get("high", price)
                    bar_low = q.get("low", price)
                    if not price or price <= 0:
                        continue

                    entry_max = c.get("entry_max", 0)
                    entry_min = c.get("entry_min", 0)

                    # Check if entry zone overlaps with bar's [low, high] range.
                    # 2% buffer: if bar barely misses entry zone, fill at bar close.
                    entry_buffer = entry_max * 0.02
                    if bar_low > entry_max + entry_buffer:
                        continue
                    if entry_min > 0 and bar_high < entry_min - entry_buffer:
                        continue
                    # If within buffer but not in zone, use bar close as fill price
                    if bar_low > entry_max:
                        price = price  # use bar close (already set)
                    elif entry_min > 0 and bar_high < entry_min:
                        price = price  # use bar close

                    # Progressive hierarchical refinement for precise entry price
                    # Zoom: coarse bar → 5m → 1m to find the first intersecting bar
                    refined_resolution = 0
                    refined_time_str = ""
                    if self.data_feed and self.mode == "backtest" and minute_ts is not None:
                        refined = self.data_feed.refine_entry(code, minute_ts, entry_min, entry_max)
                        if refined:
                            price, refined_resolution, refined_time = refined
                            refined_time_str = refined_time.strftime("%Y-%m-%d %H:%M:%S")
                        elif not (entry_min <= price <= entry_max):
                            # [low, high] overlap confirmed a trade was possible,
                            # but refinement failed (no finer data). Clamp close
                            # to the nearer bound of the entry zone as fill price.
                            if price > entry_max:
                                price = entry_max
                            elif entry_min > 0 and price < entry_min:
                                price = entry_min

                    # Defensive: if price is still bad after refinement, skip
                    if not price or price <= 0:
                        continue

                    target_pct = c.get("position_pct", 10)
                    target_value = total_value * (target_pct / 100.0)
                    if target_value > bucket_remaining:
                        continue

                    # Volatility filter: skip stocks with avg daily range < 1.8%
                    # Rationale: mega-cap banks (1.0-1.5% range) can't deliver
                    # meaningful returns within the holding period. Mid-caps at 2%+ pass.
                    avg_daily_range_pct = self._get_avg_daily_range_pct(code)
                    if avg_daily_range_pct is not None and avg_daily_range_pct < 1.8:
                        self._reject_candidate(
                            events,
                            code,
                            "low_volatility",
                            f"波动率不足: 日均振幅{avg_daily_range_pct:.1f}%<1.8%, 持仓期内难以获利",
                            strategy_type,
                        )
                        continue

                    shares = round_lot(int(target_value / price))
                    if shares < 100:
                        continue

                    stop_loss_pct = c.get("stop_loss_pct")
                    default_stop_pct = cfg["default_stop_pct"]
                    if stop_loss_pct is not None:
                        stop_loss = round(price * (1 + float(stop_loss_pct) / 100), 2)
                    else:
                        stop_loss = round(price * (1 + default_stop_pct / 100), 2)

                    if stop_loss >= price:
                        self._reject_candidate(
                            events,
                            code,
                            "invalid_stop_loss",
                            f"止损价{stop_loss}>=买入价{price}，拒绝开仓",
                            strategy_type,
                        )
                        continue

                    take_profit_pct = c.get("take_profit_pct")
                    if take_profit_pct is not None:
                        take_profit = round(price * (1 + float(take_profit_pct) / 100), 2)
                    else:
                        take_profit = round(price * 1.15, 2)

                    cand_detail = (
                        f"策略={strategy_type} | 桶={cfg['name']} | "
                        f"止损={stop_loss}({stop_loss_pct or default_stop_pct}%) 止盈={take_profit}"
                    )
                    if refined_resolution:
                        cand_detail += f" | 精度={refined_resolution}m {refined_time_str}"
                    result = self.execution.execute_buy(
                        code, shares, price,
                        strategy=c.get("strategy", ""),
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        reasoning=c.get("reasoning", ""),
                        signal_detail=cand_detail,
                        refined_resolution=refined_resolution,
                        entry_bar_ts=refined_time_str,
                        strategy_type=strategy_type,
                    )
                    if result.get("status") == "executed":
                        self._daily_new_positions += 1
                        events.append({
                            "event": "candidate_buy",
                            "code": code,
                            "price": price,
                            "shares": shares,
                            "source": c.get("source", ""),
                            "bucket": cfg["name"],
                            "strategy_type": strategy_type,
                            "result": result,
                        })
                        bucket_remaining -= target_value

        # 3. Rule signal scan (holdings + candidates only)
        try:
            from alphaclaude.tools.signal_rules import scan_code

            def _scan_one(code):
                try:
                    hist_df = None
                    if self.data_feed and self.mode == "backtest":
                        hist_df = self.data_feed.get_history_up_to(
                            code, pd.Timestamp(now.strftime("%Y-%m-%d")))
                    return code, scan_code(code, df=hist_df)
                except Exception:
                    return code, {"signals": []}

            with ThreadPoolExecutor(max_workers=min(len(codes), 10)) as ex:
                scan_futures = {ex.submit(_scan_one, c): c for c in codes}
                scan_results = {}
                for f in as_completed(scan_futures):
                    code, res = f.result()
                    scan_results[code] = res
        except Exception:
            scan_results = {}

        if scan_results:
            for code, res in scan_results.items():
                try:
                    holdings = self.state.holdings.get(code, {})
                    already_holding = holdings.get("shares", 0) > 0

                    variant = self.plan.get_variant()
                    min_conf = variant.get("signal_min_confidence", 65)
                    sig_pos_pct = variant.get("signal_position_pct", 0.075)

                    for sig in res.get("signals", []):
                        if sig.get("confidence", 0) < min_conf:
                            continue

                        rule_name = sig.get("rule", "")
                        action = sig.get("action", "alert")
                        q = quotes.get(code, {})
                        price = q.get("price", 0)
                        if not price or price <= 0:
                            continue

                        # Dedup: same (code, rule) only once per day
                        fired = self._signal_history.setdefault(code, set())
                        if rule_name in fired:
                            continue
                        if len(fired) >= 1:
                            continue
                        fired.add(rule_name)

                        if action == "buy":
                            if already_holding:
                                continue
                            if self.plan.is_on_cooldown(code):
                                continue
                            if code in self.plan.get_stopped_out_today():
                                continue
                            # Volatility filter (same as Step 2 — prevents bypass)
                            avg_range = self._get_avg_daily_range_pct(code)
                            if avg_range is not None and avg_range < 1.8:
                                continue
                            sat_value = self.state.total_value * sig_pos_pct
                            shares = round_lot(int(sat_value / price))
                            if shares < 100:
                                continue
                            sl = sig.get("suggested_stop", price * 0.95)
                            tp = price * 1.10
                            detail = self._build_signal_detail(sig)
                            result = self.execution.execute_buy(
                                code, shares, price,
                                strategy=rule_name,
                                stop_loss=sl, take_profit=tp,
                                reasoning=f"Rule: {rule_name}",
                                signal_detail=detail,
                            )
                            if result.get("status") == "executed":
                                events.append({
                                    "event": "rule_signal_buy",
                                    "code": code,
                                    "signal": sig,
                                    "result": result,
                                })

                        elif action == "sell":
                            if not already_holding:
                                continue
                            h_shares = holdings.get("available", 0)
                            if h_shares <= 0:
                                continue
                            detail = self._build_signal_detail(sig)
                            result = self.execution.execute_sell(
                                code, h_shares, price,
                                reason=f"Rule: {rule_name}",
                                signal_detail=detail,
                            )
                            if result.get("status") == "executed":
                                events.append({
                                    "event": "rule_signal_sell",
                                    "code": code,
                                    "signal": sig,
                                    "result": result,
                                })
                                # Bearish rule signals (ma_death_cross, alignment_turn_bearish) trigger cooldown
                                if rule_name in ("ma_death_cross", "alignment_turn_bearish"):
                                    c = self.plan.get_candidate(code) or {}
                                    cooldown_hours = int(c.get("cooldown_days", 1) * 24)
                                    self.plan.mark_stopped_out(code, cooldown_hours)

                        else:  # alert
                            events.append({
                                "event": "rule_signal_alert",
                                "code": code,
                                "signal": sig,
                            })
                except Exception:
                    pass

        # 4. Emergency detection
        emergency, trigger_reason = self._check_emergency(quotes)

        # Update data_time and NAV — only snapshot at EOD for clean curve
        if self.mode == "backtest":
            self.state.set_data_time(now.strftime("%Y-%m-%d %H:%M:%S"))
        else:
            self.state.set_data_time(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        if now.strftime("%H:%M:%S") == "15:00:00":
            self.state.snapshot_nav()
        self.state.save()

        # Update previous market price for next tick's emergency comparison
        if current_market_price > 0:
            self._prev_market_price = current_market_price

        if self.workflow:
            try:
                self.workflow.record_node_finish(
                    phase="intraday",
                    node_id="fastlane_tick",
                    node_name="盘中快车道",
                    summary=f"tick 完成，监控 {len(codes)} 只，事件 {len(events)} 条",
                    output_refs=["state.json", "ledger.jsonl"],
                    output_payload={"events": events[:20], "codes": codes},
                )
            except Exception as exc:
                print(f"[Workflow] record failed: {exc}")

        return {"events": events, "emergency": emergency, "trigger_reason": trigger_reason}

    def execute_holding_adjustments(self) -> list[dict]:
        """Execute plan.json holding_adjustments at market open. Called once at 9:25."""
        if self._adjustments_executed:
            return []
        self._adjustments_executed = True

        results = []
        for adj in self.plan.get_holding_adjustments():
            code = adj.get("code", "")
            action = adj.get("action", "hold")

            if action == "raise_stop":
                new_sl = adj.get("new_stop_loss", 0)
                if new_sl > 0:
                    self.plan.update_stop(code, new_sl, updated_by="execution")
                    results.append({"code": code, "action": "raise_stop", "new_stop_loss": new_sl})

            elif action == "reduce":
                h = self.state.holdings.get(code, {})
                shares = h.get("shares", 0)
                reduce_qty = shares // 2  # Reduce by half
                if reduce_qty >= 100:
                    price = h.get("current_price", 0)
                    if price > 0:
                        r = self.execution.execute_sell(code, reduce_qty, price, reason="plan_reduce")
                        results.append({"code": code, "action": "reduce", "shares": reduce_qty, "result": r})

            elif action == "close":
                h = self.state.holdings.get(code, {})
                shares = h.get("shares", 0)
                if shares >= 100:
                    price = h.get("current_price", 0)
                    if price > 0:
                        r = self.execution.execute_sell(code, shares, price, reason="plan_close")
                        results.append({"code": code, "action": "close", "shares": shares, "result": r})
                        # Prevent same-day re-entry after plan close
                        c = self.plan.get_candidate(code) or {}
                        cooldown_hours = int(c.get("cooldown_days", 1) * 24)
                        self.plan.mark_stopped_out(code, cooldown_hours)

        # Load T+0 configs from adjustments (even for "hold" actions with t0_config)
        self._load_t0_configs()

        self.plan._data["holding_adjustments"] = []  # Clear executed adjustments
        self.plan.save("execution")
        return results

    # Tiered emergency thresholds: (drop_pct, label, severity)
    _EMERGENCY_TIERS = [
        (5.0, "⚠️ 预警", 1),
        (7.5, "🔶 恶化", 2),
        (10.0, "🔴 接近跌停", 3),
    ]

    def _check_emergency(self, quotes: dict) -> tuple:
        """Tiered emergency detection. Each code fires at most once per severity tier.

        Returns (is_emergency: bool, reason: str).
        """
        triggers = self.plan.get_emergency_triggers()
        market_drop_pct = triggers.get("market_drop_pct", 3.0)
        account_drawdown_pct = triggers.get("account_drawdown_pct", 10.0)

        # Market index drop (once per day)
        market_q = quotes.get("000001", {})
        current_market_price = market_q.get("price", 0)
        if current_market_price > 0 and self._prev_market_price > 0 and "market" not in self._emergency_tiers:
            drop_pct = (self._prev_market_price - current_market_price) / self._prev_market_price * 100
            if drop_pct >= market_drop_pct:
                self._emergency_tiers["market"] = 1
                self.plan.mark_emergency_tier("market", 1)
                return True, (
                    f"大盘下跌{drop_pct:.1f}% "
                    f"(从{self._prev_market_price:.2f}至{current_market_price:.2f})"
                )

        # Account drawdown (once per day)
        if self.state.initial_capital > 0 and "account" not in self._emergency_tiers:
            drawdown = (self.state.total_value - self.state.initial_capital) / self.state.initial_capital * 100
            if drawdown <= -account_drawdown_pct:
                self._emergency_tiers["account"] = 1
                self.plan.mark_emergency_tier("account", 1)
                return True, (
                    f"账户回撤{abs(drawdown):.1f}% "
                    f"(总资产{self.state.total_value:,.0f}，"
                    f"初始资金{self.state.initial_capital:,.0f})"
                )

        # Individual holdings: tiered escalation
        for code, h in self.state.holdings.items():
            current = h.get("current_price", 0)
            cost = h.get("avg_cost", 0)
            if not (cost > 0 and current > 0):
                continue
            drop_pct = (cost - current) / cost * 100

            last_tier = self._emergency_tiers.get(code, 0)
            for threshold, label, tier in self._EMERGENCY_TIERS:
                if tier <= last_tier:
                    continue  # Already fired at this level or higher
                if drop_pct >= threshold:
                    self._emergency_tiers[code] = tier
                    self.plan.mark_emergency_tier(code, tier)
                    return True, (
                        f"{label} {code} 下跌{drop_pct:.1f}% "
                        f"(成本{cost:.2f} 现价{current:.2f})"
                    )

        return False, ""

    def reset_day(self) -> None:
        """Reset daily state for new trading day."""
        self._adjustments_executed = False
        self._prev_market_price = 0.0
        self._emergency_tiers = self.plan.get_emergency_tiers()
        self.plan._data["today_stopped_out"] = []  # fresh day, fresh cooldown list
        today = self.clock.now().strftime("%Y-%m-%d")
        if self._last_reset_day != today:
            self._signal_history.clear()
            self._candidate_rejections.clear()
            self._daily_new_positions = self._count_existing_daily_new_positions()
            self._last_reset_day = today
        # Reset T+0 daily state (preserve active positions across days)
        self._t0_global_pause_until = ""
        self._t0_total_pnl = 0.0
        for t in self._t0_trackers.values():
            t.rounds_done = 0
            t.paused_until = ""


# ═══════════════════════════════════════════════════════════════
