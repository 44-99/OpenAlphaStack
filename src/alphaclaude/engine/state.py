"""State persistence and basic trading math for the AlphaClaude engine."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime

from alphaclaude.engine.constants import (
    COMMISSION,
    LOT_SIZE,
    MIN_COMMISSION,
    PRICE_LIMIT_PCT,
    STAMP_DUTY,
)


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
    """Check if price is within +/-10% daily limit."""
    return abs(price - prev_close) / prev_close <= PRICE_LIMIT_PCT if prev_close > 0 else True


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
            "status": "executed",
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
            "status": "executed",
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
        """Record current NAV and return it. Deduplicates by time."""
        nav = self.total_value
        time_str = self._data.get("data_time") or datetime.now().isoformat()
        curve = self._data["nav_curve"]
        if curve and curve[-1].get("time") == time_str:
            curve[-1]["nav"] = nav
        else:
            curve.append({"time": time_str, "nav": nav})
        if len(curve) > 5000:
            self._data["nav_curve"] = curve[-2000:]
        return nav

    def set_data_time(self, dt: str) -> None:
        self._data["data_time"] = dt

    def set_engine_meta(self, **kwargs) -> None:
        """Persist engine metadata (mode, universe, backtest range, progress, etc.)."""
        if "engine_meta" not in self._data:
            self._data["engine_meta"] = {}
        self._data["engine_meta"].update(kwargs)
        self.save()
