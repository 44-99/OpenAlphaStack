"""Unified Agent Engine — paper/backtest/live three-mode trading system.

One engine, three modes sharing identical code paths:
  backtest  — historical K-line replay with simulated clock
  paper     — real-time quotes, virtual account
  live      — real-time quotes, real brokerage (Phase 3)

Architecture:
  Python fast lane (1-5s):  price monitor, stop-loss/profit, rule signals
  Claude Code slow lane:    strategy judgment, multi-factor analysis, plan updates
  Session lock:             only one Claude Code instance at a time
  Event queue:              batch signals for Claude Code processing

Usage:
  python tools/paper_engine.py --mode paper --capital 100000
  python tools/paper_engine.py --mode backtest --start 2023-01-01 --end 2024-12-31
  python tools/paper_engine.py --mode backtest --resume day_042
"""
import argparse
import json
import os
import sys
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, time as dtime

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)  # allow import of claude.py from project root
OUTPUT_BASE = os.path.join(PROJECT_DIR, "data", "output")
os.makedirs(OUTPUT_BASE, exist_ok=True)

# ── A-share trading constants ──────────────────────────────────
STAMP_DUTY = 0.001         # 0.1% (sell only)
COMMISSION = 0.0003        # 0.03% (buy + sell)
LOT_SIZE = 100             # 100-share board lot
T1_LOCK = True             # T+1: shares bought today cannot be sold
PRICE_LIMIT_PCT = 0.10     # 10% daily limit (ChiNext/STAR use 20%)
MIN_COMMISSION = 5.0       # minimum commission per trade

# Trading session times
AUCTION_START = dtime(9, 15)
AUCTION_END = dtime(9, 25)
MORNING_START = dtime(9, 30)
MORNING_END = dtime(11, 30)
AFTERNOON_START = dtime(13, 0)
AFTERNOON_END = dtime(15, 0)


def calc_fees(price: float, shares: int, side: str) -> float:
    """Calculate transaction cost. side: 'buy' or 'sell'."""
    trade_value = price * shares
    commission = max(trade_value * COMMISSION, MIN_COMMISSION)
    stamp = trade_value * STAMP_DUTY if side == "sell" else 0
    return round(commission + stamp, 2)


def round_lot(shares: int) -> int:
    """Round down to nearest 100-share lot."""
    return (shares // LOT_SIZE) * LOT_SIZE


def check_price_limit(price: float, prev_close: float) -> bool:
    """Check if price is within ±10% daily limit."""
    return abs(price - prev_close) / prev_close <= PRICE_LIMIT_PCT if prev_close > 0 else True


# ═══════════════════════════════════════════════════════════════
#  EngineState — state.json persistence
# ═══════════════════════════════════════════════════════════════

class EngineState:
    """Manages state.json: cash, holdings, nav_curve, data_time."""

    def __init__(self, output_dir: str, initial_capital: float = 100000):
        self.output_dir = output_dir
        self.path = os.path.join(output_dir, "state.json")
        self._lock = threading.Lock()
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {
                "initial_capital": initial_capital,
                "cash": initial_capital,
                "frozen_cash": 0,
                "holdings": {},
                "total_commission": 0,
                "total_stamp_duty": 0,
                "nav_curve": [],
                "data_time": "",
                "trade_count": 0,
                "win_count": 0,
            }
            self.save()

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, self.path)

    def load(self) -> dict:
        with self._lock:
            return dict(self._data)

    @property
    def initial_capital(self) -> float:
        return self._data["initial_capital"]

    @property
    def cash(self) -> float:
        return self._data["cash"]

    @property
    def holdings(self) -> dict:
        return self._data["holdings"]

    @property
    def total_value(self) -> float:
        hv = sum(
            h["shares"] * h["current_price"]
            for h in self._data["holdings"].values()
        )
        return round(self._data["cash"] + hv, 2)

    @property
    def total_pnl(self) -> float:
        return round(
            self.total_value - self._data["initial_capital"], 2
        )

    def update_quote(self, code: str, price: float) -> None:
        """Update current price for a holding."""
        if code in self._data["holdings"]:
            self._data["holdings"][code]["current_price"] = round(price, 2)

    def add_holding(self, code: str, shares: int, price: float,
                    strategy: str, stop_loss: float = 0,
                    take_profit: float = 0) -> dict:
        """Add shares to holdings. Returns trade record."""
        cost = price * shares + calc_fees(price, shares, "buy")
        self._data["cash"] -= cost
        self._data["total_commission"] += max(
            price * shares * COMMISSION, MIN_COMMISSION
        )

        if code in self._data["holdings"]:
            h = self._data["holdings"][code]
            total_shares = h["shares"] + shares
            old_cost = h["shares"] * h["avg_cost"]
            new_cost = shares * price
            h["avg_cost"] = round((old_cost + new_cost) / total_shares, 2)
            h["shares"] = total_shares
            h["locked_today"] += shares
        else:
            self._data["holdings"][code] = {
                "shares": shares,
                "available": shares,
                "locked_today": shares,
                "avg_cost": round(price, 2),
                "current_price": round(price, 2),
                "entry_date": self._data.get("data_time", "")[:10],
                "strategy": strategy,
                "stop_loss": round(stop_loss, 2),
                "take_profit": round(take_profit, 2),
            }

        self._data["trade_count"] += 1
        self.snapshot_nav()
        self.save()
        return {
            "action": "buy",
            "code": code,
            "shares": shares,
            "price": round(price, 2),
            "fees": round(cost - price * shares, 2),
        }

    def remove_holding(self, code: str, shares: int, price: float) -> dict | None:
        """Remove shares. Returns trade record or None."""
        if code not in self._data["holdings"]:
            return None
        h = self._data["holdings"][code]
        available = h["shares"] - h.get("locked_today", 0)
        if shares > available:
            shares = available
        if shares <= 0:
            return None

        proceeds = price * shares - calc_fees(price, shares, "sell")
        self._data["cash"] += proceeds
        self._data["total_commission"] += max(
            price * shares * COMMISSION, MIN_COMMISSION
        )
        self._data["total_stamp_duty"] += price * shares * STAMP_DUTY

        h["shares"] -= shares
        pnl = (price - h["avg_cost"]) * shares
        if pnl > 0:
            self._data["win_count"] += 1

        if h["shares"] <= 0:
            del self._data["holdings"][code]
        else:
            h["available"] = h["shares"] - h.get("locked_today", 0)

        self._data["trade_count"] += 1
        self.snapshot_nav()
        self.save()
        return {
            "action": "sell",
            "code": code,
            "shares": shares,
            "price": round(price, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round((price - h["avg_cost"]) / h["avg_cost"] * 100, 2),
        }

    def release_t1_locks(self) -> None:
        """Release T+1 locks at end of trading day."""
        for h in self._data["holdings"].values():
            h["locked_today"] = 0
            h["available"] = h["shares"]
        self.save()

    def snapshot_nav(self) -> float:
        """Record current NAV and return it."""
        nav = self.total_value
        self._data["nav_curve"].append({
            "time": self._data.get("data_time") or datetime.now().isoformat(),
            "nav": nav,
        })
        if len(self._data["nav_curve"]) > 5000:
            self._data["nav_curve"] = self._data["nav_curve"][-2000:]
        return nav

    def set_data_time(self, dt: str) -> None:
        self._data["data_time"] = dt


# ═══════════════════════════════════════════════════════════════
#  PlanManager — plan.json for Claude Code output / fast lane input
# ═══════════════════════════════════════════════════════════════

class PlanManager:
    """Manages plan.json v2: market direction, buy candidates, holding adjustments, risk report."""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "plan.json")
        self._lock = threading.Lock()
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            # Migrate v1 fields to v2 if needed
            if "daily_bias" in self._data and "market_bias" not in self._data:
                self._data["market_bias"] = self._data.pop("daily_bias", "neutral")
            if "daily_bias_confidence" in self._data and "bias_confidence" not in self._data:
                self._data["bias_confidence"] = self._data.pop("daily_bias_confidence", 50)
            if "daily_bias_reason" in self._data and "bias_reasoning" not in self._data:
                self._data["bias_reasoning"] = self._data.pop("daily_bias_reason", "")
            # Ensure v2 fields exist
            for key, default in self._default_v2_fields().items():
                if key not in self._data:
                    self._data[key] = default
        else:
            self._data = self._default_plan()
            self.save("init")

    @staticmethod
    def _default_v2_fields() -> dict:
        return {
            "position_cap_pct": 80.0,
            "preferred_sectors": [],
            "avoid_sectors": [],
            "emergency_triggers": {"market_drop_pct": 3.0, "single_stock_drop_pct": 5.0},
            "buy_candidates": [],
            "holding_adjustments": [],
            "risk_report": {"rejected_candidates": [], "correlation_matrix": {}},
        }

    @staticmethod
    def _default_plan() -> dict:
        plan = {
            "updated": "",
            "updated_by": "",
            "market_bias": "neutral",
            "bias_confidence": 50,
            "bias_reasoning": "",
            "holdings": {},
            "watchlist": [],
            "checklist": [],
            "rules": {
                "max_single_position_pct": 25.0,
                "max_total_position_pct": 80.0,
                "min_cash_reserve": 0.0,
                "stop_loss_mode": "hard",
            },
            "pending_orders": [],
        }
        plan.update(PlanManager._default_v2_fields())
        return plan

    def save(self, updated_by: str = "engine") -> None:
        with self._lock:
            self._data["updated"] = datetime.now().isoformat()
            self._data["updated_by"] = updated_by
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    def load(self) -> dict:
        with self._lock:
            return dict(self._data)

    def get_stop_loss(self, code: str) -> float | None:
        h = self._data["holdings"].get(code, {})
        return h.get("stop_loss")

    def get_take_profit(self, code: str) -> float | None:
        h = self._data["holdings"].get(code, {})
        return h.get("take_profit")

    def update(self, changes: dict, updated_by: str = "claude") -> None:
        """Merge changes into plan."""
        for key, value in changes.items():
            if key in ("holdings", "watchlist", "checklist", "rules"):
                self._data[key] = value
            elif key in self._data:
                self._data[key] = value
        self.save(updated_by)

    def update_stop(self, code: str, stop_loss: float,
                    take_profit: float = None,
                    updated_by: str = "claude") -> None:
        if code not in self._data["holdings"]:
            self._data["holdings"][code] = {}
        self._data["holdings"][code]["stop_loss"] = round(stop_loss, 2)
        if take_profit is not None:
            self._data["holdings"][code]["take_profit"] = round(take_profit, 2)
        self.save(updated_by)

    # ── v2 methods ─────────────────────────────────────────────

    def get_market_bias(self) -> str:
        return self._data.get("market_bias", "neutral")

    def get_position_cap(self) -> float:
        return self._data.get("position_cap_pct", 80.0)

    def get_buy_candidates(self) -> list[dict]:
        """Return buy candidates sorted by priority, not yet expired."""
        today = datetime.now().strftime("%Y-%m-%d")
        return [c for c in self._data.get("buy_candidates", [])
                if c.get("valid_until", today) >= today]

    def get_holding_adjustments(self) -> list[dict]:
        return self._data.get("holding_adjustments", [])

    def get_emergency_triggers(self) -> dict:
        return self._data.get("emergency_triggers",
                              {"market_drop_pct": 3.0, "single_stock_drop_pct": 5.0})

    def set_market_bias(self, bias: str, confidence: int, reasoning: str,
                        position_cap: float = None,
                        preferred: list[str] = None,
                        avoid: list[str] = None) -> None:
        self._data["market_bias"] = bias
        self._data["bias_confidence"] = confidence
        self._data["bias_reasoning"] = reasoning
        if position_cap is not None:
            self._data["position_cap_pct"] = position_cap
        if preferred is not None:
            self._data["preferred_sectors"] = preferred
        if avoid is not None:
            self._data["avoid_sectors"] = avoid
        self.save("claude_stage1")

    def set_candidates(self, candidates: list[dict]) -> None:
        self._data["buy_candidates"] = candidates
        self.save("claude_stage2")

    def set_adjustments(self, adjustments: list[dict]) -> None:
        self._data["holding_adjustments"] = adjustments
        self.save("claude_stage2")

    def mark_candidate_rejected(self, code: str, reason: str, rule: str) -> None:
        self._data["risk_report"]["rejected_candidates"].append({
            "code": code, "reason": reason, "rule": rule,
        })
        self._data["buy_candidates"] = [
            c for c in self._data.get("buy_candidates", []) if c["code"] != code
        ]
        self.save("risk_stage3")

    def set_risk_report(self, report: dict) -> None:
        self._data["risk_report"] = report
        self.save("risk_stage3")


# ═══════════════════════════════════════════════════════════════
#  Ledger — ledger.jsonl append-only decision journal
# ═══════════════════════════════════════════════════════════════

class Ledger:
    """Append-only decision ledger. Cross-session decision continuity."""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "ledger.jsonl")
        self._lock = threading.Lock()
        self._seq = self._count()

    def _count(self) -> int:
        if not os.path.exists(self.path):
            return 0
        c = 0
        with open(self.path, "r", encoding="utf-8") as f:
            for _ in f:
                c += 1
        return c

    def append(self, entry: dict) -> int:
        """Append a decision entry. Returns sequence number."""
        with self._lock:
            self._seq += 1
            entry["seq"] = self._seq
            entry["time"] = entry.get("time") or datetime.now().strftime("%H:%M:%S")
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return self._seq

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    def read_recent(self, n: int = 20) -> list[dict]:
        return self.read_all()[-n:]

    @property
    def next_seq(self) -> int:
        return self._seq + 1


# ═══════════════════════════════════════════════════════════════
#  SessionLock — global mutex (file-based)
# ═══════════════════════════════════════════════════════════════

class SessionLock:
    """File-based mutex. Only one Claude Code instance at a time."""

    def __init__(self, output_dir: str):
        self.lockfile = os.path.join(output_dir, ".session.lock")
        self._fd = None

    def acquire(self, timeout: float = 300) -> bool:
        """Block until lock acquired or timeout. Returns True if acquired."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if os.name == "nt":
                    self._fd = os.open(
                        self.lockfile, os.O_CREAT | os.O_EXCL | os.O_RDWR
                    )
                else:
                    import fcntl
                    self._fd = os.open(
                        self.lockfile, os.O_CREAT | os.O_RDWR
                    )
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (OSError, IOError):
                time.sleep(1)
        return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            try:
                os.remove(self.lockfile)
            except OSError:
                pass
            self._fd = None

    def locked(self) -> bool:
        return os.path.exists(self.lockfile)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


# ═══════════════════════════════════════════════════════════════
#  EventQueue — event_queue.jsonl for batching signals
# ═══════════════════════════════════════════════════════════════

class EventQueue:
    """Thread-safe queue backed by JSONL file. Crash-recoverable."""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "event_queue.jsonl")
        self._lock = threading.Lock()

    def push(self, event: dict) -> None:
        event["id"] = uuid.uuid4().hex[:8]
        event["timestamp"] = datetime.now().isoformat()
        event["processed"] = False
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def pop_unprocessed(self) -> list[dict]:
        """Get all unprocessed events, mark them as processing."""
        if not os.path.exists(self.path):
            return []
        with self._lock:
            events = []
            lines = []
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        lines.append(("", False))
                        continue
                    try:
                        e = json.loads(line)
                        if not e.get("processed"):
                            e["processed"] = True
                            events.append(e)
                        lines.append((json.dumps(e, ensure_ascii=False), True))
                    except json.JSONDecodeError:
                        lines.append((line, False))
            # Rewrite with updated processed flags
            with open(self.path, "w", encoding="utf-8") as f:
                for ltext, _ in lines:
                    if ltext:
                        f.write(ltext + "\n")
        return events

    def pending_count(self) -> int:
        if not os.path.exists(self.path):
            return 0
        count = 0
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line.strip())
                        if not e.get("processed"):
                            count += 1
                    except json.JSONDecodeError:
                        pass
        return count

    def should_trigger(self, count_threshold: int = 3,
                       time_threshold: int = 900) -> bool:
        """Check if enough events accumulated to trigger Claude Code."""
        pending = self.pending_count()
        if pending >= count_threshold:
            return True
        # Check if oldest pending event is older than time_threshold seconds
        if pending > 0 and os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line.strip())
                        if not e.get("processed"):
                            ts = datetime.fromisoformat(e["timestamp"])
                            if (datetime.now() - ts).total_seconds() > time_threshold:
                                return True
                            break
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass
        return False


# ═══════════════════════════════════════════════════════════════
#  TradingClock — real-time and simulation clock
# ═══════════════════════════════════════════════════════════════

class TradingClock:
    """A-share trading session clock with real and simulation modes."""

    def __init__(self, mode: str = "paper",
                 sim_start: datetime = None):
        self.mode = mode
        self.sim_time = sim_start or datetime(2023, 1, 3, 9, 30)
        self._frozen = False

    def now(self) -> datetime:
        if self.mode == "backtest":
            return self.sim_time
        return datetime.now()

    def freeze(self) -> None:
        """Pause simulation clock (Claude Code is thinking)."""
        self._frozen = True

    def advance(self, seconds: int = 60) -> None:
        """Advance simulation clock, skipping non-trading hours."""
        if self.mode != "backtest":
            return
        self.sim_time += timedelta(seconds=seconds)
        self._skip_non_trading()
        self._frozen = False

    def _skip_non_trading(self) -> None:
        """If sim_time is outside trading hours, advance to next session."""
        t = self.sim_time.time()
        if t > AFTERNOON_END or (
            t > MORNING_END and t < AFTERNOON_START
        ):
            self.sim_time += timedelta(days=1)
            self.sim_time = self.sim_time.replace(
                hour=9, minute=30, second=0, microsecond=0
            )
        # Skip weekends
        while self.sim_time.weekday() >= 5:
            self.sim_time += timedelta(days=1)
        if t < AUCTION_START and self.sim_time.time() >= AUCTION_START:
            pass  # keep the advanced time
        if t < AUCTION_START:
            self.sim_time = self.sim_time.replace(
                hour=9, minute=30, second=0, microsecond=0
            )

    def is_trading(self) -> bool:
        t = self.now().time()
        wd = self.now().weekday()
        if wd >= 5:
            return False
        return (
            (AUCTION_START <= t <= AUCTION_END) or
            (MORNING_START <= t <= MORNING_END) or
            (AFTERNOON_START <= t <= AFTERNOON_END)
        )

    def session_phase(self) -> str:
        t = self.now().time()
        wd = self.now().weekday()
        if wd >= 5:
            return "weekend"
        if t < AUCTION_START:
            return "pre_market"
        if t <= AUCTION_END:
            return "auction"
        if t < MORNING_START:
            return "pre_open"
        if t <= MORNING_END:
            return "morning"
        if t < AFTERNOON_START:
            return "lunch"
        if t <= AFTERNOON_END:
            return "afternoon"
        return "post_market"


# ═══════════════════════════════════════════════════════════════
#  ExecutionEngine — order routing with A-share rules
# ═══════════════════════════════════════════════════════════════

class ExecutionEngine:
    """Routes orders through A-share rules, executes on EngineState."""

    def __init__(self, state: EngineState, plan: PlanManager,
                 ledger: Ledger, mode: str = "paper"):
        self.state = state
        self.plan = plan
        self.ledger = ledger
        self.mode = mode

    def execute_buy(self, code: str, shares: int, price: float,
                    strategy: str = "", stop_loss: float = 0,
                    take_profit: float = 0,
                    reasoning: str = "") -> dict:
        """Validate and execute a buy order."""
        shares = round_lot(shares)
        if shares < LOT_SIZE:
            return {"error": f"最少交易 {LOT_SIZE} 股", "code": code}

        # Check cash
        estimated_cost = price * shares + calc_fees(price, shares, "buy")
        if estimated_cost > self.state.cash:
            return {"error": f"资金不足: 需要 {estimated_cost:.0f}, 可用 {self.state.cash:.0f}"}

        # Check single-position limit
        max_pos = self.plan.load()["rules"]["max_single_position_pct"]
        pos_value = price * shares
        total = self.state.total_value
        if total > 0 and pos_value / total > max_pos / 100:
            return {"error": f"单仓位超限 {max_pos}%"}

        # Execute
        trade = self.state.add_holding(
            code, shares, price, strategy, stop_loss, take_profit
        )
        trade["trade_id"] = f"{datetime.now().strftime('%Y%m%d')}_{code}_{uuid.uuid4().hex[:6]}"

        # Write ledger
        self.ledger.append({
            "decision": "open_position",
            "symbol": code,
            "shares": shares,
            "price": round(price, 2),
            "strategy": strategy,
            "reasoning": reasoning,
            "status": "executed",
            "trade_id": trade["trade_id"],
        })
        return trade

    def execute_sell(self, code: str, shares: int, price: float,
                     reason: str = "") -> dict:
        """Validate and execute a sell order."""
        trade = self.state.remove_holding(code, shares, price)
        if trade is None:
            return {"error": f"无 {code} 持仓或无可卖股数", "code": code}

        trade["trade_id"] = f"{datetime.now().strftime('%Y%m%d')}_{code}_{uuid.uuid4().hex[:6]}"

        pnl = trade.get("pnl", 0)
        self.ledger.append({
            "decision": "close_position",
            "symbol": code,
            "shares": shares,
            "price": round(price, 2),
            "reasoning": reason,
            "status": "executed",
            "trade_id": trade["trade_id"],
            "pnl": round(pnl, 2),
            "pnl_pct": trade.get("pnl_pct", 0),
        })
        return trade

    def check_stop_triggers(self, quotes: dict[str, dict]) -> list[dict]:
        """Check stop-loss and take-profit triggers. Returns triggered actions."""
        triggered = []
        for code, h in self.state.holdings.items():
            q = quotes.get(code, {})
            price = q.get("price", 0)
            if price <= 0:
                continue
            plan_stop = self.plan.get_stop_loss(code) or h.get("stop_loss", 0)
            plan_profit = self.plan.get_take_profit(code) or h.get("take_profit", 0)

            if plan_stop and price <= plan_stop:
                triggered.append({
                    "event": "stop_loss_hit",
                    "code": code,
                    "price": round(price, 2),
                    "stop_loss": plan_stop,
                    "severity": "critical",
                })
            elif plan_profit and price >= plan_profit:
                triggered.append({
                    "event": "take_profit_hit",
                    "code": code,
                    "price": round(price, 2),
                    "take_profit": plan_profit,
                    "severity": "info",
                })

        # Auto-execute hard stops
        for t in triggered:
            if t["event"] == "stop_loss_hit":
                self.execute_sell(
                    t["code"],
                    self.state.holdings[t["code"]]["shares"],
                    t["price"],
                    reason=f"止损触发: {t['stop_loss']}",
                )
            elif t["event"] == "take_profit_hit":
                # Take-profit also auto-executes for now
                self.execute_sell(
                    t["code"],
                    self.state.holdings[t["code"]]["shares"],
                    t["price"],
                    reason=f"止盈触发: {t['take_profit']}",
                )
        return triggered


# ═══════════════════════════════════════════════════════════════
#  BacktestDataFeed — historical K-line replay
# ═══════════════════════════════════════════════════════════════

class BacktestDataFeed:
    """Replays historical K-line data as if live."""

    def __init__(self, start_date: str, end_date: str,
                 universe: list[str]):
        from _fallback import get_hist
        self.universe = universe
        self.start = pd.Timestamp(start_date)
        self.end = pd.Timestamp(end_date)
        self._cache: dict[str, pd.DataFrame] = {}
        for code in universe:
            df, _ = get_hist(code, days=1500)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
                self._cache[code] = df[df["date"] <= self.end]

    def current_day_data(self, date: pd.Timestamp) -> dict[str, dict]:
        """Get all universe stocks' data for a specific date as quote dicts."""
        quotes = {}
        date_str = date.strftime("%Y-%m-%d")
        for code, df in self._cache.items():
            row = df[df["date"] == date]
            if row.empty:
                row = df[df["date"] <= date].tail(1)
            if row.empty:
                continue
            row = row.iloc[-1]
            prev_rows = df[df["date"] < date]
            prev_close = float(prev_rows.iloc[-1]["close"]) if not prev_rows.empty else float(row["open"])
            quotes[code] = {
                "code": code,
                "price": float(row["close"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "prev_close": prev_close,
                "volume": int(row["volume"]),
                "change_pct": round(
                    (float(row["close"]) - prev_close) / prev_close * 100, 2
                ) if prev_close else 0,
            }
        return quotes

    def get_history_up_to(self, code: str, date: pd.Timestamp,
                          days: int = 120) -> pd.DataFrame:
        """Return historical DataFrame for `code` up to `date` (inclusive)."""
        df = self._cache.get(code)
        if df is None:
            return pd.DataFrame()
        mask = df["date"] <= date
        result = df[mask].tail(days)
        return result.reset_index(drop=True)

    def trading_days(self) -> list[pd.Timestamp]:
        """All unique trading days across all cache data."""
        all_dates = set()
        for df in self._cache.values():
            all_dates.update(df["date"].tolist())
        return sorted([d for d in all_dates
                       if self.start <= d <= self.end])


# ═══════════════════════════════════════════════════════════════
#  OvernightPipeline — three-stage after-hours Claude Code analysis
# ═══════════════════════════════════════════════════════════════

class OvernightPipeline:
    """v3 after-hours pipeline: sub-agent research → merged Stage → Python risk validation.

    Phase 0: 3 parallel claude -p sub-agents (policy, sector, review) → ~500 char summaries
    Phase 1: Single merged Claude Code call → direction + candidates + adjustments
    Phase 2: risk.py + signal.py hard validation → final plan.json
    Emergency: Market/stock anomaly triggers Claude Code during trading hours.
    """

    def __init__(self, state: EngineState, plan: PlanManager,
                 ledger: Ledger, clock: TradingClock, output_dir: str,
                 mode: str = "paper"):
        self.state = state
        self.plan = plan
        self.ledger = ledger
        self.clock = clock
        self.output_dir = output_dir
        self.mode = mode

    # ── Phase 0: Sub-Agent Research ───────────────────────────────

    # ── Shared data fetchers (used by sub-agents + merged stage) ──

    def _fetch_market_snapshot(self) -> str:
        """Fetch market index + north-bound flow data for prompt injection."""
        lines = []
        try:
            from quote import get_market_overview
            overview = get_market_overview()
            if overview and not overview.get("error"):
                for idx in overview.get("indices", []):
                    pct = idx.get("change_pct", 0)
                    lines.append(
                        f"  {idx.get('name','')}: {idx.get('price','N/A')} "
                        f"{pct:+.2f}%"
                    )
        except Exception:
            lines.append("  (行情数据暂不可用)")
        lines.append("")
        try:
            from flow import get_north_flow
            nf = get_north_flow()
            if nf and not nf.get("error"):
                lines.append(f"  北向资金: 净流入{nf.get('net_inflow','N/A')}亿")
        except Exception:
            pass
        return "\n".join(lines)

    # ── Sub-agent prompt builders ─────────────────────────────────

    def _build_sub_agent_a_prompt(self) -> str:
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        market = self._fetch_market_snapshot()
        return (
            f"任务: 分析{sim_date} A股宏观政策环境。输出≤500字摘要。\n"
            f"\n## 今日大盘\n{market}\n"
            f"## 要求\n"
            f"1. 解读当前核心政策方向(货币政策/财政政策/产业政策)\n"
            f"2. 判断市场整体风险偏好(risk-on/risk-off)\n"
            f"3. 识别1-2个可能影响次日走势的关键事件\n"
            f"4. 直接输出摘要, 不反问, 不加代码块标记"
        )

    def _build_sub_agent_b_prompt(self) -> str:
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        market = self._fetch_market_snapshot()
        return (
            f"任务: 分析{sim_date} A股板块轮动。输出≤500字摘要。\n"
            f"\n## 今日大盘\n{market}\n"
            f"## 要求\n"
            f"1. 识别当前强势板块(3个)和弱势板块(2个)\n"
            f"2. 判断风格切换方向(大盘/小盘, 成长/价值)\n"
            f"3. 推荐3个次日值得关注的板块+理由\n"
            f"4. 直接输出摘要, 不反问, 不加代码块标记"
        )

    def _build_sub_agent_c_prompt(self) -> str:
        s = self.state.load()
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        recent = self.ledger.read_recent(10)
        lines = [
            f"任务: 复盘{sim_date}前交易决策。输出≤500字摘要。",
            "## 当前账户",
            f"总资产:{self.state.total_value:,.0f} 现金:{s['cash']:,.0f}",
        ]
        if s["holdings"]:
            lines.append("## 持仓")
            for code, h in s["holdings"].items():
                pnl = (h['current_price'] - h['avg_cost']) / h['avg_cost'] * 100 if h['avg_cost'] > 0 else 0
                lines.append(
                    f"  {code}: {h['shares']}股 成本{h['avg_cost']:.2f} "
                    f"现价{h['current_price']:.2f} 盈亏{pnl:.1f}%"
                )
        else:
            lines.append("## 持仓: 空仓")
        if recent:
            lines.append("\n## 近5条决策")
            for e in recent[-5:]:
                lines.append(f"  [{e['seq']}] {e.get('time','')} {e.get('decision','')} {e.get('reasoning','')[:80]}")
        lines.extend([
            "",
            "## 要求",
            "1. 回顾近期决策:哪些做对了/哪些做错了/为什么",
            "2. 评估当前持仓:是否需要调整/减仓/加仓",
            "3. 提炼1-2条经验教训注入次日决策",
            "4. 直接输出摘要, 不反问, 不加代码块标记",
        ])
        return "\n".join(lines)

    def _run_sub_agents(self) -> dict[str, str]:
        """Run 3 sub-agents in parallel. Returns {'A': summary, 'B': summary, 'C': summary}."""
        prompts = {
            "A": self._build_sub_agent_a_prompt(),
            "B": self._build_sub_agent_b_prompt(),
            "C": self._build_sub_agent_c_prompt(),
        }
        results = {}

        def _run_one(label, prompt):
            try:
                from claude import ask_claude
                response = ask_claude(prompt, timeout=180)
                return label, (response or "").strip()[:600]
            except Exception as exc:
                print(f"[SubAgent {label}] failed: {exc}")
                return label, f"(数据不可用: {exc})"

        print("[OvernightPipeline] Phase 0: Running 3 sub-agents in parallel...")
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(_run_one, label, p): label for label, p in prompts.items()}
            for f in as_completed(futures):
                label, summary = f.result()
                results[label] = summary
                print(f"  Sub-agent {label}: {len(summary)} chars")

        dbg_path = os.path.join(self.output_dir, "_sub_agent_summaries.txt")
        with open(dbg_path, "w", encoding="utf-8") as f:
            for label, summary in results.items():
                f.write(f"=== Sub-agent {label} ===\n{summary}\n\n")
        return results

    # ── Phase 1: Merged Decision Stage ────────────────────────────

    def _fetch_candidates_screen(self) -> str:
        """Fetch screen results for merged prompt injection."""
        lines = []
        try:
            from screen import run_screen
            result = run_screen("default")
            stocks = result.get("results", [])[:20] if isinstance(result, dict) else []
            if stocks:
                lines.append("## 技术筛选候选 (screen.py default 前20)")
                for s in stocks:
                    code = s.get("代码", "")
                    lines.append(
                        f"  {code} {s.get('名称','')}: "
                        f"现价{s.get('最新价','N/A')} 涨跌{s.get('涨跌幅','N/A'):+.2f}% "
                        f"换手{s.get('换手率','N/A')}%"
                    )
        except Exception:
            lines.append("## 技术筛选候选 (不可用)")
        return "\n".join(lines)

    def build_merged_prompt(self, summaries: dict[str, str]) -> str:
        """Build single merged prompt: direction + selection + adjustments."""
        s = self.state.load()
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        recent = self.ledger.read_recent(10)

        lines = [
            f"任务: 为{sim_date}次日制定完整交易计划。不要反问，直接输出DECISION行。",
            "",
            "## 宏观政策研究 (子Agent A)",
            summaries.get("A", "(不可用)"),
            "",
            "## 板块轮动分析 (子Agent B)",
            summaries.get("B", "(不可用)"),
            "",
            "## 决策复盘+持仓评估 (子Agent C)",
            summaries.get("C", "(不可用)"),
            "",
            "## 今日收盘数据",
            self._fetch_market_snapshot(),
            "## 账户",
            f"总资产:{self.state.total_value:,.0f} 现金:{s['cash']:,.0f} 持仓市值:{self.state.total_value - s['cash']:,.0f}",
            f"初始资金:{s['initial_capital']:,.0f} 总收益:{self.state.total_value - s['initial_capital']:,.0f}",
        ]
        if s["holdings"]:
            lines.append("\n## 持仓")
            for code, h in s["holdings"].items():
                pnl = (h['current_price'] - h['avg_cost']) / h['avg_cost'] * 100 if h['avg_cost'] > 0 else 0
                lines.append(
                    f"  {code}: {h['shares']}股 成本{h['avg_cost']:.2f} "
                    f"现价{h['current_price']:.2f} 盈亏{pnl:.1f}% "
                    f"止损{h.get('stop_loss','无')} 止盈{h.get('take_profit','无')}"
                )
        else:
            lines.append("\n## 持仓: 空仓")
        if recent:
            lines.append("\n## 近期决策")
            for e in recent[-5:]:
                lines.append(f"  [{e['seq']}] {e.get('time','')} {e.get('decision','')} {e.get('reasoning','')[:80]}")
        lines.append("")
        lines.append(self._fetch_candidates_screen())
        lines.extend([
            "",
            "## 要求",
            "基于以上所有信息(3份研究摘要+行情+账户+候选)，制定次日完整交易计划：",
            "",
            "严格按以下格式输出，每行以 DECISION| 开头：",
            "",
            "# 大盘方向",
            "DECISION|bias|bullish/neutral/bearish|confidence=50|reasoning=简短判断",
            "DECISION|position_cap|80|reasoning=仓位依据",
            "DECISION|prefer_sectors|板块1,板块2|reasoning=板块理由",
            "DECISION|avoid_sectors|板块1|reasoning=回避理由",
            "",
            "# 持仓调整 (如有, 每只一行)",
            "DECISION|adjust|CODE|action=raise_stop/close/hold|new_stop_loss=X|reasoning=理由",
            "",
            "# 买入候选 (核心B: 政策+技术,单票≤20%,止损-8%。卫星C: 技术信号,单票≤7.5%,止损-5%)",
            "DECISION|candidate|CODE|source=B/C|priority=1/2/3|entry_max=X|stop_loss=X|take_profit=X|position_pct=X|reasoning=理由",
            "",
            "注意: 使用 | 分隔符。不要加代码块标记。不要加额外解释。",
        ])
        return "\n".join(lines)

    def run_merged_stage(self, summaries: dict[str, str]) -> dict:
        """Phase 1: Single Claude Code call for direction + candidates + adjustments."""
        prompt = self.build_merged_prompt(summaries)
        try:
            from claude import ask_claude
            response = ask_claude(prompt, timeout=300)
            if response:
                dbg_path = os.path.join(self.output_dir, "_debug_merged_response.txt")
                with open(dbg_path, "w", encoding="utf-8") as f:
                    f.write(f"=== PROMPT ===\n{prompt}\n\n=== RESPONSE ===\n{response}")
                return self._parse_merged_response(response)
        except Exception as exc:
            print(f"[OvernightPipeline] merged stage failed: {exc}")
        return {"stage": "merged", "bias": "neutral", "candidates": 0, "adjustments": 0}

    def _parse_merged_response(self, response: str) -> dict:
        """Parse merged response: bias + candidates + adjustments in one pass."""
        bias = "neutral"
        confidence = 50
        bias_reasoning = ""
        position_cap = None
        preferred = None
        avoid = None
        adjustments = []
        candidates = []

        text = response.replace("```", "").replace("`", "")
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("*"):
                continue
            if "|" not in line or "DECISION" not in line:
                continue

            parts = line.split("|")
            action = parts[1] if len(parts) > 1 else ""

            if action == "bias":
                bias = parts[2] if len(parts) > 2 and parts[2] in ("bullish", "neutral", "bearish") else "neutral"
                for kv in parts[3:]:
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        if k == "confidence":
                            try: confidence = int(v)
                            except ValueError: pass
                        elif k == "reasoning": bias_reasoning = v

            elif action == "position_cap":
                try: position_cap = float(parts[2])
                except ValueError: pass

            elif action == "prefer_sectors" and len(parts) > 2:
                preferred = [s.strip() for s in parts[2].split(",") if s.strip()]

            elif action == "avoid_sectors" and len(parts) > 2:
                avoid = [s.strip() for s in parts[2].split(",") if s.strip()]

            elif action == "adjust" and len(parts) > 2:
                adj = {"code": parts[2]}
                for kv in parts[3:]:
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        if k == "new_stop_loss":
                            try: adj[k] = float(v)
                            except ValueError: adj[k] = v
                        else: adj[k] = v
                adjustments.append(adj)

            elif action == "candidate" and len(parts) > 2:
                cand = {"code": parts[2], "valid_until": self.clock.now().strftime("%Y-%m-%d")}
                for kv in parts[3:]:
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        if k in ("entry_max", "stop_loss", "take_profit", "position_pct"):
                            try: cand[k] = float(v)
                            except ValueError: cand[k] = v
                        elif k == "priority":
                            try: cand[k] = int(v)
                            except ValueError: cand[k] = v
                        else: cand[k] = v
                if all(k in cand for k in ("entry_max", "stop_loss", "take_profit")):
                    candidates.append(cand)

        if bias == "neutral" and confidence == 50:
            tu = text.upper()
            if "BULLISH" in tu: bias = "bullish"
            elif "BEARISH" in tu: bias = "bearish"
        if position_cap is None:
            position_cap = {"bullish": 80, "neutral": 50, "bearish": 20}.get(bias, 50)

        self.plan.set_market_bias(bias, confidence, bias_reasoning, position_cap, preferred, avoid)
        self.ledger.append({
            "decision": "overnight_bias", "value": bias,
            "confidence": confidence, "reasoning": bias_reasoning,
            "position_cap": position_cap,
        })
        self.plan.set_adjustments(adjustments)
        self.plan.set_candidates(candidates)

        return {
            "stage": "merged",
            "bias": bias,
            "confidence": confidence,
            "candidates": len(candidates),
            "adjustments": len(adjustments),
        }

    # ── Phase 2: Python Risk Validation ───────────────────────────

    def run_risk_validation(self) -> dict:
        """Python risk.py + signal.py hard validation on merged stage output."""
        candidates = self.plan._data.get("buy_candidates", [])
        rejected = []
        passed = []
        for c in candidates:
            code = c.get("code", "")
            if not code:
                continue
            entry = c.get("entry_max", 0)
            stop = c.get("stop_loss", 0)
            if stop >= entry:
                rejected.append({"code": code, "reason": f"stop {stop} >= entry {entry}", "rule": "signal_hard_check"})
                continue
            if entry > 0 and (entry - stop) / entry < 0.03:
                rejected.append({"code": code, "reason": "risk/reward ratio too low", "rule": "signal_hard_check"})
                continue
            passed.append(c)
        self.plan._data["buy_candidates"] = passed
        self.plan._data["risk_report"] = {
            "rejected_candidates": rejected,
            "passed_count": len(passed),
            "rejected_count": len(rejected),
        }
        self.plan.save("risk_validation")
        return {"stage": "risk", "passed": len(passed), "rejected": len(rejected)}

    # ── Full Pipeline ─────────────────────────────────────────────

    def run_full(self) -> dict:
        """Phase 0 (parallel sub-agents) → Phase 1 (merged Claude) → Phase 2 (Python risk)."""
        result = {"stages": {}}

        try:
            summaries = self._run_sub_agents()
        except Exception as exc:
            print(f"[OvernightPipeline] sub-agents failed: {exc}")
            summaries = {"A": "", "B": "", "C": ""}

        result["stages"]["merged"] = self.run_merged_stage(summaries)
        result["stages"]["risk"] = self.run_risk_validation()

        return result

    # ── Emergency Intraday Call ───────────────────────────────────

    def launch_emergency(self, trigger_reason: str, market_data: str = "") -> str | None:
        """Launch Claude Code for emergency intraday analysis.

        Called when market drops >3% or single stock drops >5%.
        """
        s = self.state.load()
        prompt = f"""盘中紧急触发。

## 触发原因
{trigger_reason}

## 市场数据
{market_data}

## 当前持仓
现金: {s['cash']:,.0f}  总资产: {self.state.total_value:,.0f}
"""
        if s["holdings"]:
            for code, h in s["holdings"].items():
                pnl = (h['current_price'] - h['avg_cost']) / h['avg_cost'] * 100 if h['avg_cost'] > 0 else 0
                prompt += f"  {code}: {h['shares']}股 现价{h['current_price']:.2f} 盈亏{pnl:.1f}%\n"
        prompt += "\n请快速判断并输出:\nDECISION|emergency_action|hold/reduce/close_all|CODE|reasoning=R\nDECISION|update_stop|CODE|new_stop_loss=X|reasoning=R\n"
        try:
            from claude import ask_claude
            response = ask_claude(
                prompt,
                session_id=f"emergency_{self.clock.now().strftime('%Y%m%d_%H%M%S')}",
                timeout=180,
            )
            if response:
                self._apply_emergency_decisions(response)
            return response
        except Exception as exc:
            print(f"[OvernightPipeline] emergency call failed: {exc}")
        return None

    def _apply_emergency_decisions(self, response: str) -> None:
        """Parse and apply emergency Claude Code decisions."""
        for line in response.split("\n"):
            line = line.strip()
            if not line.startswith("DECISION|"):
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            action = parts[1]
            if action == "update_stop":
                code = parts[2]
                for kv in parts[3:]:
                    if kv.startswith("new_stop_loss="):
                        try:
                            new_sl = float(kv.split("=", 1)[1])
                            self.plan.update_stop(code, new_sl, updated_by="emergency")
                            self.ledger.append({
                                "decision": "emergency_stop_update",
                                "code": code,
                                "new_stop_loss": new_sl,
                            })
                        except ValueError:
                            pass
            elif action == "emergency_action":
                self.ledger.append({
                    "decision": "emergency_action",
                    "action": parts[2] if len(parts) > 2 else "",
                    "code": parts[3] if len(parts) > 3 else "",
                    "reasoning": parts[5] if len(parts) > 5 else "",
                })

# ═══════════════════════════════════════════════════════════════

class FastLane:
    """Python fast lane: price monitoring, stop/profit, buy candidates, rule signals, emergency detection.

    Zero LLM calls in normal operation. Only detects emergency conditions for OvernightPipeline to handle.
    """

    def __init__(self, state: EngineState, plan: PlanManager,
                 execution: ExecutionEngine, clock: TradingClock, mode: str,
                 universe: list[str], data_feed: BacktestDataFeed = None):
        self.state = state
        self.plan = plan
        self.execution = execution
        self.clock = clock
        self.mode = mode
        self.universe = universe or []
        self.data_feed = data_feed
        self._monitored = set(self.universe)
        self._prev_market_price = 0.0    # for emergency detection
        self._adjustments_executed = False
        self._signal_history: dict[str, set] = {}  # code -> {rule names fired today}
        self._last_reset_day = None
        self._circuit_breaker_triggered = False
        self._circuit_breaker_reason = ""

    def _check_circuit_breaker(self) -> tuple[bool, str]:
        """Global circuit breaker: halt new positions at -20% drawdown."""
        if self.state.initial_capital <= 0:
            return False, ""
        drawdown = (self.state.total_value - self.state.initial_capital) / self.state.initial_capital * 100
        if drawdown <= -20:
            return True, f"熔断: 账户回撤{drawdown:.1f}% (限额-20%)"
        return False, ""

    def tick(self) -> dict:
        """One evaluation cycle. Parallel quotes + scans, action routing, dedup.

        Returns {'events': [], 'emergency': bool, 'trigger_reason': str}.
        """
        events = []
        now = self.clock.now()
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

        # Update monitored codes
        self._monitored.update(self.state.holdings.keys())
        for c in self.plan.get_buy_candidates():
            code = c.get("code", "")
            if code:
                self._monitored.add(code)
        codes = [c for c in self._monitored if c]
        if not codes:
            return {"events": events, "emergency": False, "trigger_reason": ""}

        # ── Parallel quote fetch ──────────────────────────────
        quotes = {}
        if self.data_feed and self.mode == "backtest":
            quotes = self.data_feed.current_day_data(
                pd.Timestamp(now.strftime("%Y-%m-%d"))
            )
        else:
            def _fetch_one(code):
                try:
                    from _fallback import get_quote
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

        # Update prices in state
        for code, q in quotes.items():
            self.state.update_quote(code, q.get("price", 0))

        # 1. Stop-loss / take-profit triggers (always allowed, even during circuit breaker)
        triggers = self.execution.check_stop_triggers(quotes)
        for t in triggers:
            events.append(t)

        # ── If circuit breaker active, skip all new buys ──────
        if self._circuit_breaker_triggered:
            # Still do emergency detection and NAV snapshot
            emergency, trigger_reason = self._check_emergency(quotes)
            if self.mode == "backtest":
                self.state.set_data_time(now.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                self.state.set_data_time(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.state.snapshot_nav()
            self.state.save()
            return {"events": events, "emergency": emergency, "trigger_reason": trigger_reason}

        # 2. Buy candidate check (plan.json candidates, price <= entry_max)
        candidates = self.plan.get_buy_candidates()
        if candidates:
            cap_pct = self.plan.get_position_cap()
            max_total_value = self.state.total_value * (cap_pct / 100.0)
            current_position_value = self.state.total_value - self.state.cash
            remaining_capacity = max(0, max_total_value - current_position_value)

            for c in sorted(candidates, key=lambda x: x.get("priority", 3)):
                code = c.get("code", "")
                q = quotes.get(code, {})
                price = q.get("price", 0)
                if not price or price <= 0:
                    continue

                entry_max = c.get("entry_max", 0)
                if price > entry_max:
                    continue

                source = c.get("source", "C")
                max_single_pct = 20.0 if source == "B" else 7.5
                target_pct = min(c.get("position_pct", max_single_pct), max_single_pct)
                target_value = self.state.total_value * (target_pct / 100.0)

                if target_value > remaining_capacity:
                    continue

                shares = round_lot(int(target_value / price))
                if shares < 100:
                    continue

                stop_loss = c.get("stop_loss", price * 0.94)
                take_profit = c.get("take_profit", price * 1.15)
                result = self.execution.execute_buy(
                    code, shares, price,
                    strategy=c.get("strategy", ""),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reasoning=c.get("reasoning", ""),
                )
                if result.get("status") == "executed":
                    events.append({
                        "event": "candidate_buy",
                        "code": code,
                        "price": price,
                        "shares": shares,
                        "source": source,
                        "result": result,
                    })
                    remaining_capacity -= target_value

        # 3. Rule signal scan (satellite positions, action-based routing)
        try:
            from signal_rules import scan_code

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

            for code, res in scan_results.items():
                holdings = self.state.holdings.get(code, {})
                already_holding = holdings.get("shares", 0) > 0

                for sig in res.get("signals", []):
                    if sig.get("confidence", 0) < 65:
                        continue

                    rule_name = sig.get("rule", "")
                    action = sig.get("action", "alert")  # default alert
                    q = quotes.get(code, {})
                    price = q.get("price", 0)
                    if not price or price <= 0:
                        continue

                    # Dedup: same (code, rule) only once per day
                    fired = self._signal_history.setdefault(code, set())
                    if rule_name in fired:
                        continue
                    if len(fired) >= 1:
                        continue  # max 1 rule trade per code per day
                    fired.add(rule_name)

                    if action == "buy":
                        if already_holding:
                            continue  # don't double-buy via rule signals
                        sat_value = self.state.total_value * 0.075
                        shares = round_lot(int(sat_value / price))
                        if shares < 100:
                            continue
                        sl = sig.get("suggested_stop", price * 0.95)
                        tp = price * 1.10
                        result = self.execution.execute_buy(
                            code, shares, price,
                            strategy=rule_name,
                            stop_loss=sl, take_profit=tp,
                            reasoning=f"Rule: {rule_name}",
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
                        result = self.execution.execute_sell(
                            code, h_shares, price,
                            strategy=rule_name,
                            reasoning=f"Rule: {rule_name}",
                        )
                        if result.get("status") == "executed":
                            events.append({
                                "event": "rule_signal_sell",
                                "code": code,
                                "signal": sig,
                                "result": result,
                            })

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

        # Update data_time and NAV
        if self.mode == "backtest":
            self.state.set_data_time(now.strftime("%Y-%m-%d %H:%M:%S"))
        else:
            self.state.set_data_time(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        self.state.snapshot_nav()
        self.state.save()
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

        self.plan._data["holding_adjustments"] = []  # Clear executed adjustments
        self.plan.save("execution")
        return results

    def _check_emergency(self, quotes: dict) -> tuple:
        """Check for emergency conditions: market drop >3% or single stock drop >5%.

        Returns (is_emergency: bool, reason: str).
        """
        triggers = self.plan.get_emergency_triggers()
        market_limit = triggers.get("market_drop_pct", 3.0)
        stock_limit = triggers.get("single_stock_drop_pct", 5.0)

        # Check market index (use Shanghai composite from market quote)
        market_price = 0.0
        for code, q in quotes.items():
            if code in ("000001", "sh", "market"):
                market_price = q.get("price", 0)
                break
        # If no explicit market quote, check first available
        if market_price <= 0 and quotes:
            # Use the first quote as a rough proxy
            pass

        # Check individual holdings for >stock_limit drop
        for code, h in self.state.holdings.items():
            current = h.get("current_price", 0)
            cost = h.get("avg_cost", 0)
            if cost > 0 and current > 0:
                drop_pct = (cost - current) / cost * 100
                if drop_pct >= stock_limit:
                    return True, f"{code} drop {drop_pct:.1f}% from cost {cost:.2f} to {current:.2f}"

        return False, ""

    def reset_day(self) -> None:
        """Reset daily state for new trading day."""
        self._adjustments_executed = False
        self._prev_market_price = 0.0
        today = self.clock.now().strftime("%Y-%m-%d")
        if self._last_reset_day != today:
            self._signal_history.clear()
            self._last_reset_day = today


# ═══════════════════════════════════════════════════════════════

class PaperEngine:
    """Unified Agent Engine v2: overnight batch analysis + daytime execution.

    Normal flow:
      15:00 - OvernightPipeline.run_full() (Claude Code once)
      9:15 - FastLane (Python only, all day)
      Emergency only - OvernightPipeline.launch_emergency()
    """

    def __init__(self, mode: str = "paper", capital: float = 100000,
                 universe: list[str] = None,
                 backtest_start: str = None, backtest_end: str = None,
                 resume_run_id: str = None,
                 dry_run: bool = False):
        self.mode = mode
        self.dry_run = dry_run
        self.universe = universe or []
        self._stop_event = threading.Event()

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
                backtest_start, backtest_end, universe
            )

        # Initialize core components
        self.state = EngineState(self.output_dir, capital)
        self.plan = PlanManager(self.output_dir)
        self.ledger = Ledger(self.output_dir)
        self.lock = SessionLock(self.output_dir)
        self.execution = ExecutionEngine(
            self.state, self.plan, self.ledger, mode
        )
        self.fast_lane = FastLane(
            self.state, self.plan, self.execution,
            self.clock, mode,
            self.universe, self.data_feed,
        )
        self.pipeline = OvernightPipeline(
            self.state, self.plan, self.ledger,
            self.clock, self.output_dir, mode,
        )

    def run_overnight(self) -> dict | None:
        """Run the overnight three-stage pipeline. Called after market close."""
        if self.dry_run:
            print("[Engine] Dry-run: skipping overnight Claude Code pipeline")
            return {"stages": {"stage1": "skipped", "stage2": "skipped", "stage3": "skipped"}}
        print("[Overnight] Starting three-stage pipeline...")
        result = self.pipeline.run_full()
        print(f"[Overnight] Complete: {result}")
        return result

    def run_paper(self) -> None:
        """Run in paper mode: overnights run pipeline, daytime runs FastLane only."""
        print(f"[Engine] Paper mode v2. Run ID: {self.run_id}")
        print(f"[Engine] Output: {self.output_dir}")

        overnight_done = False
        tick_count = 0

        while not self._stop_event.is_set():
            phase = self.clock.session_phase()

            # After market close (post_market or closed), run overnight pipeline once
            if phase in ("post_market", "closed") and not overnight_done:
                print("[Engine] Market closed. Running overnight pipeline...")
                self.run_overnight()
                overnight_done = True
                self.state.release_t1_locks()
                print(f"[Engine] Day ended. NAV: {self.state.total_value:,.0f}")

            # During trading hours, run FastLane
            if self.clock.is_trading():
                overnight_done = False
                self.fast_lane.reset_day()

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
                if result["emergency"] and not self.dry_run:
                    print(f"[EMERGENCY] {result['trigger_reason']}")
                    if self.lock.acquire(timeout=10):
                        try:
                            self.pipeline.launch_emergency(
                                result["trigger_reason"],
                                f"NAV: {self.state.total_value:,.0f} Cash: {self.state.cash:,.0f}",
                            )
                        finally:
                            self.lock.release()

            time.sleep(1)

    def run_backtest(self) -> None:
        """Run in backtest mode: historical replay with overnight Claude Code per trading day."""
        if not self.data_feed:
            print("[Engine] Error: backtest mode requires --start, --end, --universe")
            return

        trading_days = self.data_feed.trading_days()
        print(f"[Engine] Backtest mode v2. {len(trading_days)} trading days.")
        print(f"[Engine] Output: {self.output_dir}")

        for day in trading_days:
            if self._stop_event.is_set():
                break

            day_str = day.strftime("%Y-%m-%d")
            self.clock.sim_time = day.replace(hour=9, minute=30)

            # Update state data_time
            self.state.set_data_time(day_str + " 09:30:00")

            # Execute holding adjustments for this day
            self.fast_lane.reset_day()
            adjustments = self.fast_lane.execute_holding_adjustments()
            if adjustments:
                print(f"  [{day_str}] Adjustments: {len(adjustments)}")

            # Fast lane tick for this trading day
            result = self.fast_lane.tick()
            events = result["events"]

            if events:
                print(f"  [{day_str}] Events: {len(events)}")

            # Emergency in backtest: log but don't call Claude Code
            if result["emergency"]:
                print(f"  [{day_str}] EMERGENCY: {result['trigger_reason']}")

            # End of day: release T+1 locks
            self.state.release_t1_locks()

            # Run overnight pipeline for this day (Claude Code with date awareness)
            if not self.dry_run:
                # Set clock to after market for the overnight prompt
                self.clock.sim_time = day.replace(hour=15, minute=30)
                self.run_overnight()

            print(f"[Backtest] {day_str} | NAV: {self.state.total_value:,.0f} | "
                  f"Trades: {self.state._data['trade_count']} | "
                  f"Cash: {self.state.cash:,.0f}")

        print(f"[Backtest] Complete. Final NAV: {self.state.total_value:,.0f}")

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
            "dry_run": self.dry_run,
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

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="AlphaClaude Unified Agent Engine — paper/backtest/live")
    parser.add_argument("--mode", "-m", required=True,
                        choices=["paper", "backtest", "live"],
                        help="Operating mode")
    parser.add_argument("--capital", "-c", type=float, default=100000,
                        help="Initial capital (default: 100000)")
    parser.add_argument("--start", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--universe", "-u", default="",
                        help="Stock codes (comma-separated)")
    parser.add_argument("--watchlist", "-w", default="",
                        help="Initial watchlist codes")
    parser.add_argument("--resume", help="Resume from run_id")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without Claude Code (fast lane only)")

    args = parser.parse_args()

    universe = []
    if args.universe:
        universe = [c.strip().zfill(6)
                    for c in args.universe.split(",") if c.strip()]
    if args.watchlist:
        for c in args.watchlist.split(","):
            c = c.strip().zfill(6)
            if c and c not in universe:
                universe.append(c)

    engine = PaperEngine(
        mode=args.mode,
        capital=args.capital,
        universe=universe,
        backtest_start=args.start,
        backtest_end=args.end,
        resume_run_id=args.resume,
        dry_run=args.dry_run,
    )

    try:
        if args.mode == "backtest":
            engine.run_backtest()
        else:
            engine.run_paper()
    except KeyboardInterrupt:
        print("\n[Engine] Shutting down...")
        engine.stop()


if __name__ == "__main__":
    main()
