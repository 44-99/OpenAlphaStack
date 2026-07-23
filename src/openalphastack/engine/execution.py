"""Order execution routing for the OpenAlphaStack trading engine."""

from __future__ import annotations

import uuid
import re
from copy import deepcopy
from collections.abc import Callable
from datetime import datetime

from openalphastack.engine.constants import LOT_SIZE
from openalphastack.engine.ledger import Ledger
from openalphastack.engine.plan import PlanManager
from openalphastack.engine.state import EngineState, calc_fees, round_lot

TradeNotifier = Callable[..., None]

HARD_MAX_SINGLE_POSITION_PCT = 25.0
HARD_MAX_TOTAL_POSITION_PCT = 80.0
_STOCK_CODE_RE = re.compile(r"^\d{6}$")


class ExecutionEngine:
    """Routes orders through A-share rules, executes on EngineState."""

    def __init__(
        self,
        state: EngineState,
        plan: PlanManager,
        ledger: Ledger,
        mode: str = "paper",
        run_id: str = "",
        notify_trade_func: TradeNotifier | None = None,
    ):
        self.state = state
        self.plan = plan
        self.ledger = ledger
        self.mode = mode
        self.run_id = run_id
        self.notify_trade_func = notify_trade_func

    def execute_buy(
        self,
        code: str,
        shares: int,
        price: float,
        strategy: str = "",
        stop_loss: float = 0,
        take_profit: float = 0,
        reasoning: str = "",
        signal_detail: str = "",
        refined_resolution: int = 0,
        entry_bar_ts: str = "",
        strategy_type: str = "",
    ) -> dict:
        """Validate and execute a buy order."""
        if not _STOCK_CODE_RE.fullmatch(str(code)):
            return {"error": "证券代码必须是六位数字", "code": code}
        if price <= 0:
            return {"error": "成交价格必须大于 0", "code": code}
        shares = round_lot(shares)
        if shares < LOT_SIZE:
            return {"error": f"最少交易 {LOT_SIZE} 股", "code": code}

        estimated_cost = price * shares + calc_fees(price, shares, "buy")
        rules = self.plan.load().get("rules") or {}
        min_cash_reserve = max(0.0, float(rules.get("min_cash_reserve", 0) or 0))
        spendable_cash = max(0.0, self.state.cash - min_cash_reserve)
        if estimated_cost > spendable_cash:
            return {
                "error": (
                    f"可用资金不足: 需要 {estimated_cost:.0f}, "
                    f"扣除保留现金后可用 {spendable_cash:.0f}"
                )
            }

        max_pos = min(
            HARD_MAX_SINGLE_POSITION_PCT,
            float(rules.get("max_single_position_pct", HARD_MAX_SINGLE_POSITION_PCT)),
        )
        max_total = min(
            HARD_MAX_TOTAL_POSITION_PCT,
            float(rules.get("max_total_position_pct", HARD_MAX_TOTAL_POSITION_PCT)),
        )
        pos_value = price * shares
        total = self.state.total_value
        holding = self.state.holdings.get(code, {})
        existing_pos_value = float(holding.get("shares", 0) or 0) * float(
            holding.get("current_price", price) or price
        )
        if total > 0 and (existing_pos_value + pos_value) / total > max_pos / 100:
            return {"error": f"单仓位超限 {max_pos}%"}
        invested_value = sum(
            float(item.get("shares", 0) or 0) * float(item.get("current_price", 0) or 0)
            for item in self.state.holdings.values()
        )
        if total > 0 and (invested_value + pos_value) / total > max_total / 100:
            return {"error": f"总仓位超限 {max_total}%"}

        before = self.state.snapshot()
        trade = self.state.add_holding(
            code, shares, price, strategy, stop_loss, take_profit, persist=False
        )
        trade["trade_id"] = f"{datetime.now().strftime('%Y%m%d')}_{code}_{uuid.uuid4().hex[:6]}"

        entry = {
            "decision": "open_position",
            "symbol": code,
            "shares": shares,
            "price": round(price, 2),
            "avg_cost": round(price, 2),
            "stop_loss": round(stop_loss, 2) if stop_loss else 0,
            "take_profit": round(take_profit, 2) if take_profit else 0,
            "strategy": strategy,
            "reasoning": reasoning,
            "status": "executed",
            "trade_id": trade["trade_id"],
        }
        if strategy_type:
            entry["strategy_type"] = strategy_type
        if signal_detail:
            entry["signal_detail"] = signal_detail
        if refined_resolution:
            entry["refined_resolution"] = refined_resolution
        if entry_bar_ts:
            entry["entry_bar_ts"] = entry_bar_ts
        try:
            self.state.commit_trade(self.ledger, entry)
        except Exception:
            self.state.restore(before)
            raise
        self._notify_trade(
            "buy",
            code,
            "",
            price,
            shares,
            reason=reasoning,
            data_time=self.state._data.get("data_time", ""),
            signal_detail=signal_detail,
            run_id=self.run_id,
        )
        return trade

    def execute_sell(
        self,
        code: str,
        shares: int,
        price: float,
        reason: str = "",
        signal_detail: str = "",
    ) -> dict:
        """Validate and execute a sell order."""
        if not _STOCK_CODE_RE.fullmatch(str(code)):
            return {"error": "证券代码必须是六位数字", "code": code}
        if price <= 0:
            return {"error": "成交价格必须大于 0", "code": code}
        before = self.state.snapshot()
        trade = self.state.remove_holding(code, shares, price, persist=False)
        if trade is None:
            return {"error": f"无 {code} 持仓或无可卖股数", "code": code}

        trade["trade_id"] = f"{datetime.now().strftime('%Y%m%d')}_{code}_{uuid.uuid4().hex[:6]}"

        pnl = trade.get("pnl", 0)
        entry = {
            "decision": "close_position",
            "symbol": code,
            "shares": shares,
            "price": round(price, 2),
            "reasoning": reason,
            "status": "executed",
            "trade_id": trade["trade_id"],
            "pnl": round(pnl, 2),
            "pnl_pct": trade.get("pnl_pct", 0),
        }
        try:
            self.state.commit_trade(self.ledger, entry)
        except Exception:
            self.state.restore(before)
            raise
        action = "stop_loss" if "止损" in reason else "sell"
        self._notify_trade(
            action,
            code,
            "",
            price,
            shares,
            pnl=pnl,
            reason=reason,
            data_time=self.state._data.get("data_time", ""),
            signal_detail=signal_detail,
            run_id=self.run_id,
        )
        return trade

    def check_stop_triggers(self, quotes: dict[str, dict]) -> list[dict]:
        """Check stop-loss and take-profit triggers. Returns triggered actions.

        Validates stop direction for long positions: stop must be < avg_cost.
        Respects T+1: only sells available (non-locked) shares.
        After stop-out, marks the code in plan cooldown.
        """
        triggered = []
        for code, h in self.state.holdings.items():
            q = quotes.get(code, {})
            price = q.get("price", 0)
            if price <= 0:
                continue
            avg_cost = h.get("avg_cost", 0)
            available = h.get("available", h.get("shares", 0))
            if available <= 0:
                continue

            plan_stop = self.plan.get_stop_loss(code) or h.get("stop_loss", 0)
            plan_profit = self.plan.get_take_profit(code) or h.get("take_profit", 0)

            if plan_stop and plan_stop >= avg_cost and avg_cost > 0:
                pass
            elif plan_stop and price <= plan_stop:
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

        for t in triggered:
            h = self.state.holdings.get(t["code"], {})
            available = h.get("available", h.get("shares", 0))
            if available <= 0:
                continue
            if t["event"] == "stop_loss_hit":
                self.execute_sell(
                    t["code"],
                    available,
                    t["price"],
                    reason=f"止损触发: {t['stop_loss']}",
                )
                c = self.plan.get_candidate(t["code"]) or {}
                cooldown_hours = int(c.get("cooldown_days", 1) * 24)
                self.plan.mark_stopped_out(t["code"], cooldown_hours)
            elif t["event"] == "take_profit_hit":
                self.execute_sell(
                    t["code"],
                    available,
                    t["price"],
                    reason=f"止盈触发: {t['take_profit']}",
                )
                c = self.plan.get_candidate(t["code"]) or {}
                cooldown_hours = int(c.get("cooldown_days", 1) * 24)
                self.plan.mark_stopped_out(t["code"], cooldown_hours)
        return triggered

    def _notify_trade(self, *args, **kwargs) -> None:
        if not self.notify_trade_func:
            return
        try:
            self.notify_trade_func(*args, **kwargs)
        except Exception:
            pass
