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
  python tools/paper_engine.py --mode paper --capital 100000 --universe auto
  python tools/paper_engine.py --mode backtest --start 2023-01-01 --end 2024-12-31 --universe auto
  python tools/paper_engine.py --mode backtest --resume day_042
  python tools/paper_engine.py --mode backtest --dry-run  (Python only, no Claude Code)
"""
import argparse
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from datetime import time as dtime

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)  # allow import of claude.py from project root
OUTPUT_BASE = os.path.join(PROJECT_DIR, "data", "output")
os.makedirs(OUTPUT_BASE, exist_ok=True)

try:
    from tools.notifier import (
        notify_alert,
        notify_backtest_complete,
        notify_backtest_progress,
        notify_engine_start,
        notify_engine_stop,
        notify_overnight_complete,
        notify_overnight_timeout,
        notify_sub_agent_summaries,
        notify_trade,
        notify_trading_day_end,
    )
    _notify = True
except Exception:
    _notify = False

# ── A-share trading constants ──────────────────────────────────
STAMP_DUTY = 0.001         # 0.1% (sell only)
COMMISSION = 0.0003        # 0.03% (buy + sell)
LOT_SIZE = 100             # 100-share board lot
T1_LOCK = True             # T+1: shares bought today cannot be sold
PRICE_LIMIT_PCT = 0.10     # 10% daily limit (ChiNext/STAR use 20%)
MIN_COMMISSION = 5.0       # minimum commission per trade

# Trading session times
PRE_MARKET_START = dtime(8, 0)  # before 8am = overnight/closed
AUCTION_START = dtime(9, 15)
AUCTION_END = dtime(9, 25)
MORNING_START = dtime(9, 30)
MORNING_END = dtime(11, 30)
AFTERNOON_START = dtime(13, 0)
AFTERNOON_END = dtime(15, 0)


def generate_universe(min_daily_volume: int = 5_000_000, exclude_st: bool = True,
                      cache_path: str = None) -> list[str]:
    """Generate a filtered A-share main board universe.

    Filters: main board only (00xxxx/60xxxx), no ST/*ST, minimum daily volume.
    Caches the result to avoid repeated akshare calls.
    Returns list of 6-digit codes.
    """
    if cache_path is None:
        cache_path = os.path.join(PROJECT_DIR, "data", "cache", "universe_cache.json")
    # Return cached if fresh (< 7 days)
    if os.path.exists(cache_path):
        try:
            mtime = os.path.getmtime(cache_path)
            if time.time() - mtime < 7 * 86400:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        codes = []
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            name = str(row.get("name", ""))
            if not code.startswith(("00", "60")):
                continue
            if exclude_st and ("ST" in name or "*ST" in name):
                continue
            codes.append(code)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(codes, f)
        print(f"[Universe] Generated {len(codes)} main board stocks (cached)")
        return codes
    except Exception as e:
        print(f"[Universe] akshare failed: {e}, using fallback")
        return _fallback_universe()


def _fallback_universe() -> list[str]:
    """Fallback universe: major liquid stocks across sectors."""
    return [
        # 金融
        "000001", "002142", "600000", "600015", "600016", "600036", "601009",
        "601166", "601288", "601318", "601328", "601398", "601939", "601988",
        # 消费
        "000568", "000858", "002304", "600519", "600809", "600887", "603288",
        # 医药
        "000538", "002001", "300015", "300347", "300529", "300760", "600276",
        "603259",
        # 科技
        "000063", "000725", "002049", "002230", "002371", "002415", "300059",
        "300124", "600703", "603501",
        # 新能源
        "002074", "002129", "300014", "300274", "300450", "300750", "600438",
        "601012", "601615",
        # 周期
        "000630", "002155", "600585", "600900", "601088", "601600", "601668",
        "601857", "601899",
        # 军工
        "000547", "000768", "002013", "600150", "600391", "600760", "600893",
        # 中特估
        "001979", "600028", "600050", "600941", "601088", "601390", "601668",
        "601728", "601766", "601800", "601857",
    ]


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
            "emergency_triggers": {"market_drop_pct": 3.0, "single_stock_drop_pct": 5.0, "account_drawdown_pct": 10.0},
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

    def set_variant(self, variant: dict) -> None:
        self._data["strategy_variant"] = {
            "name": variant.get("name", "默认"),
            "source_b_max_pct": variant.get("source_b_max_pct", 20.0),
            "source_b_stop_pct": variant.get("source_b_stop_pct", -8),
            "source_c_max_pct": variant.get("source_c_max_pct", 7.5),
            "source_c_stop_pct": variant.get("source_c_stop_pct", -5),
            "max_single_position_pct": variant.get("max_single_position_pct", 25.0),
            "signal_min_confidence": variant.get("signal_min_confidence", 65),
            "signal_position_pct": variant.get("signal_position_pct", 0.075),
            "max_total_position_pct": variant.get("max_total_position_pct", 80.0),
        }
        self.save("variant")

    def get_variant(self) -> dict:
        return self._data.get("strategy_variant", {})


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
        if t < PRE_MARKET_START:
            return "closed"
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
                    reasoning: str = "", signal_detail: str = "") -> dict:
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
        if _notify:
            dt = self.state._data.get("data_time", "")
            notify_trade("buy", code, "", price, shares, reason=reasoning,
                        data_time=dt, signal_detail=signal_detail)
        return trade

    def execute_sell(self, code: str, shares: int, price: float,
                     reason: str = "", signal_detail: str = "") -> dict:
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
        if _notify:
            action = "stop_loss" if "止损" in reason else "sell"
            dt = self.state._data.get("data_time", "")
            notify_trade(action, code, "", price, shares, pnl=pnl, reason=reason,
                        data_time=dt, signal_detail=signal_detail)
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
    """Replays historical K-line data as if live. Lazy-loads data on demand."""

    def __init__(self, start_date: str, end_date: str,
                 universe: list[str]):
        self.universe = universe
        self.start = pd.Timestamp(start_date)
        self.end = pd.Timestamp(end_date)
        self._cache: dict[str, pd.DataFrame] = {}
        self._loaded: set[str] = set()
        self._load_lock = threading.Lock()

        # Pre-load first 50 stocks to have initial data; rest loaded on demand
        _preload_n = min(50, len(universe))
        print(f"[DataFeed] Loading {len(universe)} stocks (pre-fetching {_preload_n}, rest lazy)...")
        self._batch_load(universe[:_preload_n], show_progress=False)
        # Start background loading for the rest
        if len(universe) > _preload_n:
            remaining = universe[_preload_n:]
            self._bg_loaded = 0
            self._bg_total = len(remaining)
            def _bg_load():
                self._batch_load(remaining, show_progress=True)
            t = threading.Thread(target=_bg_load, daemon=True)
            t.start()

        # Fetch Shanghai Composite index for emergency detection
        self._index_cache: pd.DataFrame | None = None
        try:
            import akshare as ak
            idx_df = ak.stock_zh_index_daily(symbol="sh000001")
            if not idx_df.empty:
                idx_df = idx_df.rename(columns={
                    "日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume",
                })
                idx_df["date"] = pd.to_datetime(idx_df["date"])
                idx_df = idx_df.sort_values("date").reset_index(drop=True)
                idx_df = idx_df[idx_df["date"] <= self.end]
                self._index_cache = idx_df
        except Exception:
            self._index_cache = None

    def _load_one(self, code: str) -> pd.DataFrame:
        """Load historical data for a single code, with caching."""
        if code in self._cache:
            return self._cache[code]
        from _fallback import get_hist
        try:
            df, _ = get_hist(code, days=1500)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
                df = df[df["date"] <= self.end]
                self._cache[code] = df
                return df
        except Exception:
            pass
        self._cache[code] = pd.DataFrame()
        return pd.DataFrame()

    def _batch_load(self, codes: list[str], show_progress: bool = True) -> None:
        """Load a batch of codes, with optional progress bar."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        n = len(codes)
        done = 0
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(self._load_one, c): c for c in codes}
            for f in as_completed(futures):
                done += 1
                with self._load_lock:
                    self._loaded.add(futures[f])
                if show_progress and done % 50 == 0:
                    print(f"  [DataFeed] {done}/{n} stocks loaded...")
        if show_progress:
            print(f"  [DataFeed] All {n} stocks loaded.")

    def _ensure_loaded(self, code: str) -> None:
        """Ensure a stock's data is loaded (blocking)."""
        if code in self._cache:
            return
        self._load_one(code)
        with self._load_lock:
            self._loaded.add(code)

    def _is_loaded(self, code: str) -> bool:
        return code in self._cache

    def current_day_data(self, date: pd.Timestamp) -> dict[str, dict]:
        """Get loaded stocks' data for a specific date as quote dicts.

        Only returns data for stocks already loaded into cache.
        Use _ensure_loaded() first for stocks that must be included.
        """
        quotes = {}
        for code in list(self._cache.keys()):
            df = self._cache[code]
            if df.empty:
                continue
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
        # Include market index data for emergency detection
        index_code = "000001"
        if self._index_cache is not None:
            idx_row = self._index_cache[self._index_cache["date"] == date]
            if idx_row.empty:
                idx_row = self._index_cache[self._index_cache["date"] <= date].tail(1)
            if not idx_row.empty:
                idx_row = idx_row.iloc[-1]
                prev_idx = self._index_cache[self._index_cache["date"] < date]
                idx_prev_close = float(prev_idx.iloc[-1]["close"]) if not prev_idx.empty else float(idx_row["open"])
                quotes[index_code] = {
                    "code": index_code,
                    "price": float(idx_row["close"]),
                    "open": float(idx_row["open"]),
                    "high": float(idx_row["high"]),
                    "low": float(idx_row["low"]),
                    "prev_close": idx_prev_close,
                    "volume": int(idx_row.get("volume", 0)),
                    "change_pct": round(
                        (float(idx_row["close"]) - idx_prev_close) / idx_prev_close * 100, 2
                    ) if idx_prev_close else 0,
                }

        return quotes

    def get_history_up_to(self, code: str, date: pd.Timestamp,
                          days: int = 120) -> pd.DataFrame:
        """Return historical DataFrame for `code` up to `date` (inclusive). Lazy-loads."""
        self._ensure_loaded(code)
        df = self._cache.get(code)
        if df is None or df.empty:
            return pd.DataFrame()
        mask = df["date"] <= date
        result = df[mask].tail(days)
        return result.reset_index(drop=True)

    def trading_days(self) -> list[pd.Timestamp]:
        """All unique trading days from the index cache or a sample stock."""
        all_dates = set()
        # Use index cache if available (most reliable)
        if self._index_cache is not None:
            all_dates.update(self._index_cache["date"].tolist())
        # Also merge from loaded stocks
        for df in list(self._cache.values()):
            if not df.empty:
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
                 mode: str = "paper", execution: "ExecutionEngine" = None):
        self.state = state
        self.plan = plan
        self.ledger = ledger
        self.clock = clock
        self.output_dir = output_dir
        self.mode = mode
        self.execution = execution
        self.run_id = os.path.basename(output_dir)
        self._last_shadow_diagnostics = ""
        try:
            from tools.strategy_variants import get_active_variant
            self.variant = get_active_variant()
        except Exception:
            self.variant = {
                "name": "默认", "position_cap_by_bias": {"bullish": 80, "neutral": 50, "bearish": 20},
                "source_b_max_pct": 20.0, "source_b_stop_pct": -8,
                "source_c_max_pct": 7.5, "source_c_stop_pct": -5,
                "max_single_position_pct": 25.0, "signal_min_confidence": 65,
                "signal_position_pct": 0.075, "max_total_position_pct": 80.0,
            }

    def _bc_rules_text(self) -> str:
        """B/C source rules text from active variant for LLM prompts."""
        v = self.variant
        return (
            f"B类上限{v.get('source_b_max_pct',20):.0f}%"
            f"止损{v.get('source_b_stop_pct',-8)}%, "
            f"C类上限{v.get('source_c_max_pct',7.5):.0f}%"
            f"止损{v.get('source_c_stop_pct',-5)}%。"
        )

    # ── Phase 0: Sub-Agent Research ───────────────────────────────

    # ── Shared data fetchers (used by sub-agents + merged stage) ──

    def _fetch_market_snapshot(self) -> str:
        """Fetch market index + north-bound flow data for prompt injection."""
        lines = []
        try:
            from tools.quote import get_market_overview
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
            from tools.flow import get_north_flow
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
            f"{sim_date} A股宏观政策分析。≤500字摘要。\n"
            f"大盘:\n{market}\n"
            f"要求: 1.政策方向 2.风险偏好(risk-on/off) 3.1个关键事件\n"
            f"数据不足时基于最近市场趋势和常识合理推断，标注[推断]。"
        )

    def _build_sub_agent_b_prompt(self) -> str:
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        market = self._fetch_market_snapshot()
        return (
            f"{sim_date} A股板块轮动。≤500字摘要。\n"
            f"大盘:\n{market}\n"
            f"要求: 1.强势板块3个+弱势2个 2.风格切换(大/小盘,成长/价值) 3.次日关注板块3个+理由\n"
            f"数据不足时基于最近市场趋势和常识合理推断，标注[推断]。"
        )

    def _build_sub_agent_c_prompt(self) -> str:
        s = self.state.load()
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        recent = self.ledger.read_recent(5)
        lines = [
            f"{sim_date} 交易复盘。≤500字摘要。",
            f"总资产:{self.state.total_value:,.0f} 现金:{s['cash']:,.0f}",
            "数据不足时基于常识合理推断，标注[推断]。",
        ]

        # Try Shadow Account diagnostics when enough trades exist
        shadow_text = self._try_shadow_diagnostics()

        if shadow_text:
            lines.append(f"\n[影子账户行为诊断]\n{shadow_text}")
            lines.append("要求: 1.验证诊断是否准确 2.确认/否定每个模式 3.提出1条可操作的prompt改进建议")
            self._last_shadow_diagnostics = shadow_text
        else:
            self._last_shadow_diagnostics = ""
            if s["holdings"]:
                lines.append("持仓:")
                for code, h in s["holdings"].items():
                    pnl = (h['current_price'] - h['avg_cost']) / h['avg_cost'] * 100 if h['avg_cost'] > 0 else 0
                    lines.append(f"  {code}: {h['shares']}股 成本{h['avg_cost']:.2f} 现价{h['current_price']:.2f} {pnl:+.1f}%")
            else:
                lines.append("空仓")
            if recent:
                lines.append("近5决策:")
                for e in recent[-5:]:
                    lines.append(f"  [{e['seq']}] {e.get('decision','')} {e.get('reasoning','')[:60]}")
            lines.append("要求: 1.决策回顾 2.持仓评估 3.经验教训1条")
        return "\n".join(lines)

    def _try_shadow_diagnostics(self) -> str:
        """Compute shadow diagnostics from ledger. Returns '' if insufficient data."""
        try:
            all_entries = self.ledger.read_all()
            trade_entries = [e for e in all_entries
                           if e.get("decision") in ("open_position", "close_position")]
            if len(trade_entries) < 8:
                return ""
            from tools.shadow_account import pair_trades, compute_diagnostics, format_for_prompt
            paired, open_pos = pair_trades(all_entries)
            diagnostics = compute_diagnostics(paired, open_pos, all_entries)
            return format_for_prompt(diagnostics)
        except Exception:
            return ""

    def _run_sub_agents(self) -> dict[str, str]:
        """Run 3 sub-agents in parallel. Returns {'A': summary, 'B': summary, 'C': summary}."""
        prompts = {
            "A": self._build_sub_agent_a_prompt(),
            "B": self._build_sub_agent_b_prompt(),
            "C": self._build_sub_agent_c_prompt(),
        }
        results = {}

        def _run_one(label, prompt):
            from claude import ask_claude
            for attempt in range(2):
                try:
                    response = ask_claude(prompt, timeout=300)
                    text = (response or "").strip()
                    if text and "超时" not in text and "出错" not in text:
                        return label, text[:500]
                    if attempt == 0:
                        print(f"  Sub-agent {label} attempt {attempt+1}: timeout/error, retrying...")
                        time.sleep(5)
                    else:
                        return label, text[:500] if text else f"(失败: {text[:80]})"
                except Exception as exc:
                    if attempt == 0:
                        print(f"  Sub-agent {label} attempt {attempt+1}: {exc}, retrying...")
                        time.sleep(5)
                    else:
                        print(f"[SubAgent {label}] failed: {exc}")
                        return label, f"(数据不可用: {exc})"
            return label, "(超时)"

        print("[OvernightPipeline] Phase 0: Running 3 sub-agents in parallel...")
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(_run_one, label, p): label for label, p in prompts.items()}
            from concurrent.futures import wait as fut_wait
            done, not_done = fut_wait(futures, timeout=360)
            for f in done:
                label, summary = f.result()
                results[label] = summary
                print(f"  Sub-agent {label}: {len(summary)} chars")
            for f in not_done:
                label = futures[f]
                results[label] = "(超时)"
                f.cancel()
                print(f"  Sub-agent {label}: timed out")

        dbg_path = os.path.join(self.output_dir, "_sub_agent_summaries.txt")
        with open(dbg_path, "w", encoding="utf-8") as f:
            for label, summary in results.items():
                f.write(f"=== Sub-agent {label} ===\n{summary}\n\n")
        if _notify:
            try:
                notify_sub_agent_summaries(self.run_id, results)
            except Exception:
                pass
        return results

    # ── Phase 1: Merged Decision Stage ────────────────────────────

    def _fetch_candidates_screen(self) -> str:
        """Fetch screen results for merged prompt injection."""
        lines = []
        try:
            from tools.screen import run_screen
            result = run_screen("default")
            stocks = result.get("results", [])[:10] if isinstance(result, dict) else []
            if stocks:
                lines.append("## 技术筛选候选 (screen.py default 前10)")
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

    def _build_shadow_feedback(self) -> str:
        """Read accumulated shadow patterns, return anti-pattern rules for prompt injection."""
        import json as _json
        patterns_path = os.path.join(
            self.output_dir, "shadow_account", "patterns.json")
        if not os.path.exists(patterns_path):
            return ""
        try:
            with open(patterns_path, "r", encoding="utf-8") as _f:
                data = _json.load(_f)
        except (OSError, ValueError):
            return ""

        active = [p for p in data.get("patterns", []) if p.get("status") == "active"]
        if not active:
            return ""

        lines = ["## [影子账户] 历史重复错误模式 — 务必规避"]
        for i, p in enumerate(active[:5], 1):
            name = p.get("name", "")
            count = p.get("occurrence_count", 0)
            evidence = p.get("evidence", "")
            fix = p.get("suggested_fix", "")
            lines.append(f"{i}. [{name}] 共{count}次 {evidence}")
            if fix:
                lines.append(f"   规避措施: {fix}")
        return "\n".join(lines)

    def _call_text_safe(self, prompt: str, label: str) -> str:
        """Call call_text with error handling. Returns '' on failure."""
        from tools.llm_client import call_text
        try:
            result = call_text(prompt, max_tokens=2048)
            return (result or "").strip()
        except Exception as exc:
            print(f"[OvernightPipeline] {label} text call failed: {exc}")
            return ""

    def _parse_candidates(self, tool_inputs: list[dict]) -> list[dict]:
        """Parse TOOL_ADD_CANDIDATE results into candidate dicts."""
        candidates = []
        for d in (tool_inputs or []):
            if "code" not in d:
                continue
            c = {"code": str(d["code"])}
            for k in ("source", "reasoning"):
                if k in d:
                    c[k] = str(d[k])
            for k in ("priority",):
                if k in d:
                    try:
                        c[k] = int(d[k])
                    except (ValueError, TypeError):
                        pass
            for k in ("entry_max", "stop_loss", "take_profit", "position_pct"):
                if k in d:
                    try:
                        c[k] = float(d[k])
                    except (ValueError, TypeError):
                        pass
            candidates.append(c)
        return candidates

    def _build_bull_prompt(self, summaries: dict[str, str], direction: dict) -> str:
        """Build prompt for Bull agent: find reasons to buy each candidate."""
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        bias = direction.get("bias", "neutral")
        cap = direction.get("position_cap", 80)
        parts = [
            f"{sim_date} 你是乐观的A股分析师(Bull)。为以下候选池中每只股票找出做多理由。",
            f"市场偏向:{bias} 仓位上限:{cap}%",
            f"板块分析: {summaries.get('B','')[:200]}",
            self._fetch_candidates_screen(),
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        parts.append(
            "要求: 1.逐只分析技术面/资金面/消息面做多理由 "
            "2.给出每只的合理买入价位和止盈目标 "
            "3.不遗漏任何候选，不筛选——你只负责找理由买入"
        )
        return "\n".join(parts)

    def _build_bear_prompt(self, summaries: dict[str, str], direction: dict,
                           bull_analysis: str) -> str:
        """Build prompt for Bear agent: find risks NOT to buy each candidate."""
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        bias = direction.get("bias", "neutral")
        cap = direction.get("position_cap", 80)
        parts = [
            f"{sim_date} 你是审慎的风险分析师(Bear)。阅读Bull的做多分析后，逐只找出不应买入的理由。",
            f"市场偏向:{bias} 仓位上限:{cap}%",
            f"板块分析: {summaries.get('B','')[:200]}",
            "## Bull 做多分析",
            bull_analysis[:2000],
            "## 候选池",
            self._fetch_candidates_screen(),
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        parts.append(
            "要求: 1.逐只找出Bull遗漏的风险点(估值/技术/资金/政策) "
            "2.对每只给出风险等级(高/中/低) "
            "3.标注哪几只应直接否决——你负责挑毛病"
        )
        return "\n".join(parts)

    def _build_risk_prompt(self, summaries: dict[str, str], direction: dict,
                           bull_analysis: str, bear_analysis: str) -> str:
        """Build prompt for Risk agent: final arbiter after debate."""
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        bias = direction.get("bias", "neutral")
        preferred = ",".join(direction.get("preferred") or []) or "无"
        cap = direction.get("position_cap", 80)
        parts = [
            f"{sim_date}次日选股。你是最终决策者(Risk)。阅读Bull和Bear的辩论后，"
            f"调用 add_candidate 工具提交最终候选。",
            f"市场偏向:{bias} 偏好板块:{preferred} 仓位上限:{cap}%",
            "## Bull 做多分析",
            bull_analysis[:2000],
            "## Bear 风险分析",
            bear_analysis[:2000],
            self._fetch_candidates_screen(),
            self._bc_rules_text(),
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        parts.append(
            "裁决原则: 1.Bear标注'直接否决'的候选不纳入 "
            "2.Bull理由充分且Bear风险可控的优先 "
            "3.每只入选候选调用一次 add_candidate"
        )
        return "\n".join(parts)

    def _run_bull_bear_debate(self, summaries: dict[str, str],
                              direction: dict) -> tuple[list[dict], str]:
        """Run Bull/Bear/Risk three-stage debate for candidate selection.
        Returns (candidates, debate_trace). Falls back to empty on any failure.
        """
        from tools.llm_client import call_with_tool, TOOL_ADD_CANDIDATE

        bull_prompt = self._build_bull_prompt(summaries, direction)
        bull_text = self._call_text_safe(bull_prompt, "Bull")
        if not bull_text:
            return [], ""

        bear_prompt = self._build_bear_prompt(summaries, direction, bull_text)
        bear_text = self._call_text_safe(bear_prompt, "Bear")
        if not bear_text:
            return [], ""

        risk_prompt = self._build_risk_prompt(summaries, direction, bull_text, bear_text)
        try:
            tool_inputs = call_with_tool(risk_prompt, [TOOL_ADD_CANDIDATE])
        except Exception as exc:
            print(f"[OvernightPipeline] Risk debate call failed: {exc}")
            return [], ""

        candidates = self._parse_candidates(tool_inputs or [])
        trace = f"=== BULL ===\n{bull_text}\n\n=== BEAR ===\n{bear_text}"
        return candidates, trace

    def run_merged_stage(self, summaries: dict[str, str]) -> dict:
        """Phase 1-3: Direct API calls with Tool Use for guaranteed structured output."""
        from tools.llm_client import (
            call_with_tool,
            TOOL_SET_DIRECTION,
            TOOL_ADD_CANDIDATE,
            TOOL_ADJUST_HOLDING,
        )

        # Step 1: Direction + Sectors
        dir_prompt = self._build_direction_prompt(summaries)
        direction = {"bias": "neutral", "confidence": 50, "bias_reasoning": "",
                     "position_cap": None, "preferred": None, "avoid": None}
        try:
            tool_inputs = call_with_tool(dir_prompt, [TOOL_SET_DIRECTION])
            if tool_inputs:
                d = tool_inputs[0]
                direction = {
                    "bias": d.get("bias", "neutral"),
                    "confidence": int(d.get("confidence", 50)),
                    "bias_reasoning": str(d.get("bias_reasoning", "")),
                    "position_cap": int(d.get("position_cap", 0)) or None,
                    "preferred": d.get("prefer_sectors") if isinstance(d.get("prefer_sectors"), list) else None,
                    "avoid": d.get("avoid_sectors") if isinstance(d.get("avoid_sectors"), list) else None,
                }
        except Exception as exc:
            print(f"[OvernightPipeline] direction stage failed: {exc}")

        # Step 2: Candidates (Bull/Bear debate with single-call fallback)
        candidates = []
        debate_trace = ""
        try:
            candidates, debate_trace = self._run_bull_bear_debate(summaries, direction)
        except Exception as exc:
            print(f"[OvernightPipeline] Bull/Bear debate failed: {exc}")

        if not candidates:
            # Fallback to single-call
            candidates_prompt = self._build_candidates_prompt(summaries, direction)
            try:
                tool_inputs = call_with_tool(candidates_prompt, [TOOL_ADD_CANDIDATE])
                candidates = self._parse_candidates(tool_inputs or [])
                if not candidates:
                    if _notify:
                        notify_overnight_timeout(self.run_id,
                            f"选股返回空: {len(tool_inputs or [])}个tool call但无有效candidate")
            except Exception as exc:
                print(f"[OvernightPipeline] candidates fallback failed: {exc}")
                if _notify:
                    notify_overnight_timeout(self.run_id, f"选股阶段: {exc}")

        # Debug log
        dbg_path = os.path.join(self.output_dir, "_debug_candidates_response.txt")
        with open(dbg_path, "w", encoding="utf-8") as f:
            if debate_trace:
                f.write(f"=== DEBATE TRACE ===\n{debate_trace}\n\n")
            f.write(f"=== FINAL CANDIDATES ===\n{json.dumps(candidates, ensure_ascii=False, indent=2)}\n")

        # Step 3: Adjustments (only if holdings exist)
        adjustments = []
        if self.state.holdings:
            adj_prompt = self._build_adjustments_prompt(direction)
            try:
                tool_inputs = call_with_tool(adj_prompt, [TOOL_ADJUST_HOLDING])
                for d in tool_inputs:
                    if "code" not in d:
                        continue
                    adj = {
                        "code": str(d["code"]),
                        "action": str(d.get("action", "hold")),
                    }
                    if d.get("reasoning"):
                        adj["reasoning"] = str(d["reasoning"])
                    if "new_stop_loss" in d:
                        try:
                            adj["new_stop_loss"] = float(d["new_stop_loss"])
                        except (ValueError, TypeError):
                            pass
                    adjustments.append(adj)
            except Exception as exc:
                print(f"[OvernightPipeline] adjustments stage failed: {exc}")

        return self._apply_merged(direction, candidates, adjustments)

    def _build_direction_prompt(self, summaries: dict[str, str]) -> str:
        s = self.state.load()
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        parts = [
            f"{sim_date}次日A股方向。请调用 set_direction 工具提交判断。",
            f"宏观: {summaries.get('A','')[:200]}",
            f"板块: {summaries.get('B','')[:200]}",
            f"复盘: {summaries.get('C','')[:200]}",
            self._fetch_market_snapshot(),
            f"账户: 总{self.state.total_value:,.0f} 现金{s['cash']:,.0f}",
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        return "\n".join(parts)

    def _build_candidates_prompt(self, summaries: dict[str, str], direction: dict) -> str:
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        bias = direction.get("bias", "neutral")
        preferred = ",".join(direction.get("preferred") or []) or "无"
        cap = direction.get("position_cap", 80)
        parts = [
            f"{sim_date}次日选股。{bias}偏好{preferred}仓位{cap}%。请为每只候选调用一次 add_candidate 工具。",
            f"板块: {summaries.get('B','')[:200]}",
            self._fetch_candidates_screen(),
            self._bc_rules_text(),
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        return "\n".join(parts)

    def _build_adjustments_prompt(self, direction: dict) -> str:
        s = self.state.load()
        lines = ["根据持仓调仓。为每只持仓调用一次 adjust_holding 工具。", "## 持仓"]
        for code, h in s["holdings"].items():
            pnl = (h['current_price'] - h['avg_cost']) / h['avg_cost'] * 100 if h['avg_cost'] > 0 else 0
            lines.append(
                f"  {code}: {h['shares']}股 成本{h['avg_cost']:.2f} "
                f"现价{h['current_price']:.2f} 盈亏{pnl:.1f}% "
                f"止损{h.get('stop_loss','无')}"
            )
        return "\n".join(lines)


    def _apply_merged(self, direction: dict, candidates: list[dict],
                      adjustments: list[dict]) -> dict:
        """Apply parsed direction, candidates, and adjustments to plan."""
        bias = direction.get("bias", "neutral")
        confidence = direction.get("confidence", 50)
        bias_reasoning = direction.get("bias_reasoning", "")
        position_cap = direction.get("position_cap") or \
            self.variant.get("position_cap_by_bias", {}).get(bias, 50)
        preferred = direction.get("preferred")
        avoid = direction.get("avoid")

        self.plan.set_market_bias(bias, confidence, bias_reasoning, position_cap, preferred, avoid)
        self.plan.set_variant(self.variant)
        self.ledger.append({
            "decision": "overnight_bias", "value": bias,
            "confidence": confidence, "reasoning": bias_reasoning,
            "position_cap": position_cap,
            "variant": self.variant.get("name", "默认"),
        })
        self.plan.set_adjustments(adjustments)
        self.plan.set_candidates(candidates)

        # Apply variant rules to plan
        rules = self.plan.load().get("rules", {})
        rules["max_single_position_pct"] = self.variant.get("max_single_position_pct", 25.0)
        rules["max_total_position_pct"] = self.variant.get("max_total_position_pct", 80.0)
        self.plan.save("variant_rules")

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
        from tools.risk import (
            calc_position_size,
            calc_volatility_adjusted_limit,
            calc_volatility_metrics,
            max_drawdown_check,
        )

        candidates = self.plan._data.get("buy_candidates", [])
        rejected = []
        passed = []
        for c in candidates:
            code = c.get("code", "")
            if not code:
                continue

            # ── Hard checks (must pass) ──
            entry = c.get("entry_max", 0)
            stop = c.get("stop_loss", 0)
            if stop >= entry:
                rejected.append({"code": code, "reason": f"stop {stop} >= entry {entry}", "rule": "signal_hard_check"})
                continue
            if entry > 0 and (entry - stop) / entry < 0.03:
                rejected.append({"code": code, "reason": "risk/reward ratio too low", "rule": "signal_hard_check"})
                continue

            # ── risk.py quantitative checks (non-fatal on error) ──
            try:
                from _fallback import get_hist
                df, _ = get_hist(code, days=120)
                if df.empty:
                    rejected.append({"code": code, "reason": "no historical data", "rule": "risk_data"})
                    continue

                closes = [float(x) for x in df["close"].tolist()]
                price = closes[-1]

                # Volatility check
                vol = calc_volatility_metrics(closes)
                annualized_vol = vol["annualized_volatility"]
                limit_pct = calc_volatility_adjusted_limit(annualized_vol)

                # Position sizing — ensure candidate position doesn't exceed vol-adjusted limit
                sizing = calc_position_size(price, self.state.initial_capital, limit_pct)
                sizing_limit_pct = sizing["position_limit_pct"]
                candidate_pct = c.get("position_pct", 20)
                if candidate_pct > sizing_limit_pct:
                    rejected.append({
                        "code": code,
                        "reason": (
                            f"仓位{candidate_pct}%超出波动率调整上限{sizing_limit_pct}% "
                            f"(年化波动率{annualized_vol:.1%})"
                        ),
                        "rule": "risk_volatility",
                    })
                    continue

                # Drawdown check
                dd = max_drawdown_check(closes)
                if dd.get("warn"):
                    rejected.append({
                        "code": code,
                        "reason": (
                            f"个票回撤警告: 当前回撤{dd['current_drawdown_pct']}%，"
                            f"历史最大回撤{dd['max_historical_drawdown_pct']}%"
                        ),
                        "rule": "risk_drawdown",
                    })
                    continue

                # Inject risk-adjusted sizing
                c["position_limit_pct"] = sizing_limit_pct
                c["max_shares"] = sizing["max_shares"]
                c["volatility"] = vol
                passed.append(c)

            except Exception as exc:
                # Non-fatal: let candidate pass through on error
                print(f"[Risk] soft error for {code}: {exc}")
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

        # Persist shadow diagnostics if computed
        self._save_shadow_if_dirty(summaries.get("C", ""))

        result["stages"]["merged"] = self.run_merged_stage(summaries)
        result["stages"]["risk"] = self.run_risk_validation()

        return result

    def _save_shadow_if_dirty(self, sub_c_output: str) -> None:
        """Save shadow diagnostics if data was computed during prompt building."""
        if not self._last_shadow_diagnostics:
            return
        try:
            from tools.shadow_account import load_ledger, pair_trades, compute_diagnostics, save_diagnostics
            entries = load_ledger(self.run_id)
            if entries:
                paired, open_pos = pair_trades(entries)
                diagnostics = compute_diagnostics(paired, open_pos, entries)
                save_diagnostics(self.run_id, diagnostics, sub_c_output)
        except Exception:
            pass

    # ── Emergency Intraday Call ───────────────────────────────────

    def launch_emergency(self, trigger_reason: str, market_data: str = "") -> str | None:
        """Emergency intraday analysis via API + Tool Use for guaranteed structured output.

        Called when market drops >3% or single stock drops >5%.
        """
        if _notify:
            notify_alert("critical", "盘中紧急触发", trigger_reason)
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
        prompt += '\n请调用 emergency_action 工具提交应急决策。'
        try:
            from tools.llm_client import call_with_tool, TOOL_EMERGENCY_ACTION
            tool_inputs = call_with_tool(prompt, [TOOL_EMERGENCY_ACTION], max_tokens=2048)
            if tool_inputs:
                self._apply_emergency_decisions(tool_inputs[0])
            return json.dumps(tool_inputs, ensure_ascii=False) if tool_inputs else "(empty)"
        except Exception as exc:
            print(f"[OvernightPipeline] emergency call failed: {exc}")
        return None

    def _apply_emergency_decisions(self, data: dict) -> None:
        """Apply emergency API Tool Use response (already structured JSON)."""
        # Handle stop updates
        for upd in data.get("stop_updates") or []:
            if isinstance(upd, dict) and "code" in upd and "new_stop_loss" in upd:
                try:
                    code = str(upd["code"])
                    new_sl = float(upd["new_stop_loss"])
                    self.plan.update_stop(code, new_sl, updated_by="emergency")
                    self.ledger.append({
                        "decision": "emergency_stop_update",
                        "code": code,
                        "new_stop_loss": new_sl,
                    })
                except (ValueError, TypeError):
                    pass

        # Handle emergency action
        action_type = data.get("action", "hold")
        code_arg = str(data.get("code", ""))
        reasoning = str(data.get("reasoning", ""))

        # Execution logic
        executed = False
        if action_type == "close_all" and self.execution:
            for h_code, h in list(self.state.holdings.items()):
                shares = h.get("shares", 0)
                price = h.get("current_price", 0)
                if shares >= 100 and price > 0:
                    self.execution.execute_sell(
                        h_code, shares, price,
                        reason=f"emergency_close_all: {reasoning}",
                    )
                    executed = True
        elif action_type == "reduce" and code_arg and self.execution:
            h = self.state.holdings.get(code_arg, {})
            shares = h.get("shares", 0)
            price = h.get("current_price", 0)
            if shares >= 100 and price > 0:
                reduce_qty = (shares // 200) * 100
                if reduce_qty >= 100:
                    self.execution.execute_sell(
                        code_arg, reduce_qty, price,
                        reason=f"emergency_reduce: {reasoning}",
                    )
                    executed = True
        elif action_type == "close" and code_arg and self.execution:
            h = self.state.holdings.get(code_arg, {})
            shares = h.get("shares", 0)
            price = h.get("current_price", 0)
            if shares >= 100 and price > 0:
                self.execution.execute_sell(
                    code_arg, shares, price,
                    reason=f"emergency_close: {reasoning}",
                )
                executed = True

        self.ledger.append({
            "decision": "emergency_action",
            "action": action_type,
            "code": code_arg,
            "reasoning": reasoning,
            "executed": executed,
        })

# ═══════════════════════════════════════════════════════════════

class FastLane:
    """Python fast lane: price monitoring, stop/profit, buy candidates, rule signals, emergency detection.

    Zero LLM calls in normal operation. Only detects emergency conditions for OvernightPipeline to handle.
    """

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
                 universe: list[str], data_feed: BacktestDataFeed = None):
        self.state = state
        self.plan = plan
        self.execution = execution
        self.clock = clock
        self.mode = mode
        self.universe = universe or []
        self.data_feed = data_feed
        # Monitored = holdings + buy candidates only.
        # The full universe is Claude Code's selection pool (via screen.py), not scanned every tick.
        self._monitored = set()
        self._prev_market_price = 0.0
        self._adjustments_executed = False
        self._signal_history: dict[str, set] = {}
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

        # ── Monitored codes: holdings + buy candidates from plan ──
        # Universe is Claude Code's selection pool, not scanned mechanically.
        codes = set(self.state.holdings.keys())
        for c in self.plan.get_buy_candidates():
            code = c.get("code", "")
            if code:
                codes.add(code)

        codes = [c for c in codes if c]
        if not codes:
            return {"events": events, "emergency": False, "trigger_reason": ""}

        # ── Quote fetch ───────────────────────────────────────
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

        # Fetch market index for emergency detection (live/paper mode)
        if not (self.data_feed and self.mode == "backtest"):
            try:
                from quote import get_market_overview
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
                variant = self.plan.get_variant()
                if source == "B":
                    max_single_pct = variant.get("source_b_max_pct", 20.0)
                    default_stop_mul = 1 + variant.get("source_b_stop_pct", -8) / 100
                else:
                    max_single_pct = variant.get("source_c_max_pct", 7.5)
                    default_stop_mul = 1 + variant.get("source_c_stop_pct", -5) / 100
                target_pct = min(c.get("position_pct", max_single_pct), max_single_pct)
                target_value = self.state.total_value * (target_pct / 100.0)

                if target_value > remaining_capacity:
                    continue

                shares = round_lot(int(target_value / price))
                if shares < 100:
                    continue

                stop_loss = c.get("stop_loss", round(price * default_stop_mul, 2))
                take_profit = c.get("take_profit", price * 1.15)
                cand_detail = f"来源={source} | 优先级={c.get('priority','?')} | 止损={stop_loss} 止盈={take_profit}"
                result = self.execution.execute_buy(
                    code, shares, price,
                    strategy=c.get("strategy", ""),
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reasoning=c.get("reasoning", ""),
                    signal_detail=cand_detail,
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

        # 3. Rule signal scan (holdings + candidates only)
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

        # Update previous market price for next tick's emergency comparison
        if current_market_price > 0:
            self._prev_market_price = current_market_price

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
        """Check for emergency conditions: market -3%, account -10%, or single stock -5%.

        Returns (is_emergency: bool, reason: str).
        """
        triggers = self.plan.get_emergency_triggers()
        stock_limit = triggers.get("single_stock_drop_pct", 5.0)
        market_drop_pct = triggers.get("market_drop_pct", 3.0)
        account_drawdown_pct = triggers.get("account_drawdown_pct", 10.0)

        # Check market index drop vs previous close
        market_q = quotes.get("000001", {})
        current_market_price = market_q.get("price", 0)
        if current_market_price > 0 and self._prev_market_price > 0:
            drop_pct = (self._prev_market_price - current_market_price) / self._prev_market_price * 100
            if drop_pct >= market_drop_pct:
                return True, (
                    f"大盘下跌{drop_pct:.1f}% "
                    f"(从{self._prev_market_price:.2f}至{current_market_price:.2f}，"
                    f"触发阈值{market_drop_pct}%)"
                )

        # Check total account drawdown
        if self.state.initial_capital > 0:
            drawdown = (self.state.total_value - self.state.initial_capital) / self.state.initial_capital * 100
            if drawdown <= -account_drawdown_pct:
                return True, (
                    f"账户回撤{abs(drawdown):.1f}% "
                    f"(总资产{self.state.total_value:,.0f}，"
                    f"初始资金{self.state.initial_capital:,.0f}，"
                    f"触发阈值{account_drawdown_pct}%)"
                )

        # Check individual holdings for >stock_limit drop
        for code, h in self.state.holdings.items():
            current = h.get("current_price", 0)
            cost = h.get("avg_cost", 0)
            if cost > 0 and current > 0:
                drop_pct = (cost - current) / cost * 100
                if drop_pct >= stock_limit:
                    return True, f"{code} 个票下跌{drop_pct:.1f}% (成本{cost:.2f} 现价{current:.2f})"

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
                 dry_run: bool = False, claude_every: int = 7):
        self.mode = mode
        self.dry_run = dry_run
        self.claude_every = claude_every
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
            execution=self.execution,
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
        """Run in paper/live mode: overnights run pipeline, daytime runs FastLane only."""
        print(f"[Engine] Mode: {self.mode.upper()} | Run ID: {self.run_id}")
        print(f"[Engine] Output: {self.output_dir}")

        if _notify:
            notify_engine_start(self.mode, self.state.initial_capital)

        _last_pipeline_date = None  # date() of last overnight pipeline run
        _post_market_date = None    # date() of last post-market summary
        tick_count = 0

        while not self._stop_event.is_set():
            phase = self.clock.session_phase()
            today = self.clock.now().date()

            # Pre-market (8:00-9:15): run overnight pipeline once per day, before auction
            if phase == "pre_market" and _last_pipeline_date != today:
                print("[Engine] Pre-market. Running overnight pipeline...")
                overnight_result = self.run_overnight()
                _last_pipeline_date = today
                print("[Engine] Pipeline complete. Ready for trading day.")

                if _notify and overnight_result:
                    risk = overnight_result.get("stages", {}).get("risk", {})
                    merged = overnight_result.get("stages", {}).get("merged", {})
                    notify_overnight_complete(self.run_id, {
                        "bias": merged.get("bias", "neutral"),
                        "candidates": merged.get("candidates", 0),
                        "passed": risk.get("passed", 0),
                        "rejected": risk.get("rejected", 0),
                        "nav": self.state.total_value,
                    })

            # Post-market: daily summary and T+1 lock release (only after a pipeline has run)
            if phase == "post_market" and _post_market_date != today and _last_pipeline_date is not None:
                _post_market_date = today
                self.state.release_t1_locks()
                nav = self.state.total_value
                print(f"[Engine] Day ended. NAV: {nav:,.0f}")

                if _notify:
                    positions = self.state.holdings
                    day_pnl = sum(
                        (h.get("current_price", h.get("avg_cost", 0)) - h.get("avg_cost", 0)) * h.get("shares", 0)
                        for h in positions.values()
                    )
                    notify_trading_day_end(
                        self.run_id, nav, day_pnl,
                        (day_pnl / self.state.initial_capital * 100) if self.state.initial_capital > 0 else 0,
                        positions, self.state._data.get("trade_count", 0),
                    )

            # During trading hours, run FastLane
            if self.clock.is_trading():
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
                    if _notify:
                        notify_alert("critical", "紧急触发", result["trigger_reason"])
                    if self.lock.acquire(timeout=10):
                        try:
                            self.pipeline.launch_emergency(
                                result["trigger_reason"],
                                f"NAV: {self.state.total_value:,.0f} Cash: {self.state.cash:,.0f}",
                            )
                        finally:
                            self.lock.release()

            time.sleep(1)

    def run_backtest(self, claude_every: int = 7) -> None:
        """Run in backtest mode: historical replay with periodic Claude Code analysis.

        claude_every: run overnight pipeline every N trading days (default 7, weekly).
                     Day 1 always gets Claude Code. Set to 1 for every day.
        """
        if not self.data_feed:
            print("[Engine] Error: backtest mode requires --start, --end, --universe")
            return

        trading_days = self.data_feed.trading_days()
        total_days = len(trading_days)
        print(f"[Engine] Backtest v3. {total_days} trading days, Claude every {claude_every} day(s).")
        print(f"[Engine] Universe: {len(self.universe)} stocks")
        print(f"[Engine] Output: {self.output_dir}")

        if _notify:
            notify_engine_start(
                self.mode, self.state.initial_capital,
                start_date=trading_days[0].strftime("%Y-%m-%d") if total_days > 0 else "",
                end_date=trading_days[-1].strftime("%Y-%m-%d") if total_days > 0 else "",
            )

        last_claude_day = None
        t_start = time.time()

        for i, day in enumerate(trading_days):
            if self._stop_event.is_set():
                break

            day_str = day.strftime("%Y-%m-%d")
            self.clock.sim_time = day.replace(hour=9, minute=30)
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
            if result["emergency"]:
                print(f"  [{day_str}] EMERGENCY: {result['trigger_reason']}")

            # End of day: release T+1 locks
            self.state.release_t1_locks()

            # Run overnight pipeline periodically (day 1 + every N days)
            should_claude = (
                not self.dry_run
                and (i == 0 or (i + 1) % claude_every == 0)
            )
            if should_claude:
                self.clock.sim_time = day.replace(hour=15, minute=30)
                self.run_overnight()
                last_claude_day = day_str
                print(f"  [{day_str}] Claude Code pipeline executed")

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
              f"Last Claude Code: {last_claude_day or 'never'}. Final NAV: {self.state.total_value:,.0f}")

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
                        help="Stock codes (comma-separated) or 'auto' for main board")
    parser.add_argument("--watchlist", "-w", default="",
                        help="Initial watchlist codes")
    parser.add_argument("--resume", help="Resume from run_id")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without Claude Code (fast lane only)")
    parser.add_argument("--claude-every", type=int, default=7,
                        help="Run Claude Code every N trading days in backtest (default: 7, 1=every day)")

    args = parser.parse_args()

    # Universe resolution
    if args.universe.lower() == "auto":
        universe = generate_universe()
        print(f"[Main] Auto universe: {len(universe)} stocks")
    elif args.universe:
        universe = [c.strip().zfill(6)
                    for c in args.universe.split(",") if c.strip()]
    else:
        universe = _fallback_universe()
        print(f"[Main] Fallback universe: {len(universe)} stocks")

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
        claude_every=args.claude_every,
    )

    try:
        if args.mode == "backtest":
            engine.run_backtest(claude_every=args.claude_every)
        else:
            engine.run_paper()
    except KeyboardInterrupt:
        print("\n[Engine] Shutting down...")
        engine.stop()
        if _notify:
            notify_engine_stop(args.mode, "用户中断")
    else:
        if _notify:
            notify_engine_stop(args.mode, "正常退出")


if __name__ == "__main__":
    main()
