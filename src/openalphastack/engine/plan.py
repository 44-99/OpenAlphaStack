"""Plan persistence for externally published plans and fast-lane execution."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta


class PlanManager:
    """Manages plan.json v2: market direction, buy candidates, holding adjustments, risk report."""

    def __init__(self, output_dir: str):
        self.path = os.path.join(output_dir, "plan.json")
        self._lock = threading.Lock()
        self._now_override = None
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            if "daily_bias" in self._data and "market_bias" not in self._data:
                self._data["market_bias"] = self._data.pop("daily_bias", "neutral")
            if "daily_bias_confidence" in self._data and "bias_confidence" not in self._data:
                self._data["bias_confidence"] = self._data.pop("daily_bias_confidence", 50)
            if "daily_bias_reason" in self._data and "bias_reasoning" not in self._data:
                self._data["bias_reasoning"] = self._data.pop("daily_bias_reason", "")
            for key, default in self._default_v2_fields().items():
                if key not in self._data:
                    self._data[key] = default
        else:
            self._data = self._default_plan()
            self.save("init")
        self._last_mtime_ns = self._plan_mtime_ns()

    @property
    def _now(self) -> datetime:
        return self._now_override or datetime.now()

    def set_sim_now(self, dt: datetime) -> None:
        self._now_override = dt

    @staticmethod
    def _default_v2_fields() -> dict:
        return {
            "plan_date": "",
            "plan_generated_at": "",
            "position_cap_pct": 80.0,
            "preferred_sectors": [],
            "avoid_sectors": [],
            "emergency_triggers": {"market_drop_pct": 3.0, "single_stock_drop_pct": 5.0, "account_drawdown_pct": 10.0},
            "buy_candidates": [],
            "holding_adjustments": [],
            "risk_report": {"rejected_candidates": [], "correlation_matrix": {}},
            "cooldown": {},
            "today_stopped_out": [],
            "emergency_tiers": {"date": "", "tiers": {}},
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
                "daily_new_positions_limit": 3,
            },
            "pending_orders": [],
        }
        plan.update(PlanManager._default_v2_fields())
        return plan

    def save(self, updated_by: str = "engine") -> None:
        with self._lock:
            self._data["updated"] = self._now.isoformat()
            self._data["updated_by"] = updated_by
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            last_error: PermissionError | None = None
            for attempt in range(8):
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(min(0.02 * (2 ** attempt), 0.5))
            else:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                if last_error is not None:
                    raise last_error
            self._last_mtime_ns = self._plan_mtime_ns()

    def refresh_external(self) -> bool:
        """Reload a newer atomically-published plan from MCP.

        The engine remains the sole writer during execution, while Codex may
        publish a pre-market plan through the MCP gateway.  Modification-time
        comparison prevents an older external snapshot from overwriting newer
        in-process execution updates.
        """
        next_mtime = self._plan_mtime_ns()
        if next_mtime <= self._last_mtime_ns:
            return False
        with self._lock:
            next_mtime = self._plan_mtime_ns()
            if next_mtime <= self._last_mtime_ns:
                return False
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError, TypeError):
                return False
            if not isinstance(data, dict):
                return False
            for key, default in self._default_v2_fields().items():
                data.setdefault(key, default)
            self._data = data
            self._last_mtime_ns = next_mtime
            return True

    def _plan_mtime_ns(self) -> int:
        try:
            return os.stat(self.path).st_mtime_ns
        except OSError:
            return 0

    def load(self) -> dict:
        with self._lock:
            return dict(self._data)

    def get_stop_loss(self, code: str) -> float | None:
        h = self._data["holdings"].get(code, {})
        return h.get("stop_loss")

    def get_take_profit(self, code: str) -> float | None:
        h = self._data["holdings"].get(code, {})
        return h.get("take_profit")

    def update(self, changes: dict, updated_by: str = "external_agent") -> None:
        for key, value in changes.items():
            if key in ("holdings", "watchlist", "checklist", "rules"):
                self._data[key] = value
            elif key in self._data:
                self._data[key] = value
        self.save(updated_by)

    def update_stop(self, code: str, stop_loss: float,
                    take_profit: float = None,
                    updated_by: str = "external_agent") -> None:
        if code not in self._data["holdings"]:
            self._data["holdings"][code] = {}
        self._data["holdings"][code]["stop_loss"] = round(stop_loss, 2)
        if take_profit is not None:
            self._data["holdings"][code]["take_profit"] = round(take_profit, 2)
        self.save(updated_by)

    def get_market_bias(self) -> str:
        return self._data.get("market_bias", "neutral")

    def get_position_cap(self) -> float:
        return self._data.get("position_cap_pct", 80.0)

    def get_buy_candidates(self) -> list[dict]:
        today = self._now.strftime("%Y-%m-%d")
        return [
            self.normalize_candidate_strategy(dict(c))
            for c in self._data.get("buy_candidates", [])
            if c.get("valid_until", today) >= today
        ]

    def mark_premarket_plan_generated(self, generated_at: datetime | None = None) -> None:
        """Record the trading date that this plan was explicitly generated for."""
        dt = generated_at or self._now
        self._data["plan_date"] = dt.strftime("%Y-%m-%d")
        self._data["plan_generated_at"] = dt.isoformat()
        self.save("premarket_plan")

    def get_holding_adjustments(self) -> list[dict]:
        return self._data.get("holding_adjustments", [])

    def get_emergency_triggers(self) -> dict:
        return self._data.get("emergency_triggers",
                              {"market_drop_pct": 3.0, "single_stock_drop_pct": 5.0})

    def get_emergency_tiers(self) -> dict[str, int]:
        """Return persisted same-day emergency tiers for restart-safe dedupe."""
        today = self._now.strftime("%Y-%m-%d")
        store = self._data.setdefault("emergency_tiers", {"date": today, "tiers": {}})
        if store.get("date") != today:
            store["date"] = today
            store["tiers"] = {}
            self.save("emergency_tiers_reset")
        tiers = store.setdefault("tiers", {})
        result = {}
        for key, value in tiers.items():
            try:
                result[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
        return result

    def mark_emergency_tier(self, key: str, tier: int) -> None:
        """Persist the highest emergency tier fired today for a code/account/market."""
        today = self._now.strftime("%Y-%m-%d")
        store = self._data.setdefault("emergency_tiers", {"date": today, "tiers": {}})
        if store.get("date") != today:
            store["date"] = today
            store["tiers"] = {}
        tiers = store.setdefault("tiers", {})
        current = int(tiers.get(key, 0) or 0)
        parsed_tier = int(tier)
        if parsed_tier > current:
            tiers[key] = parsed_tier
            self.save("emergency_tier")

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
        self.save("research_stage")

    def set_candidates(self, candidates: list[dict]) -> None:
        today = self._now.strftime("%Y-%m-%d")

        def _normalize(c):
            entry_max = c.get("entry_max", 0)
            sl_pct = c.get("stop_loss_pct")
            if sl_pct is not None:
                if entry_max > 0:
                    c["stop_loss"] = round(entry_max * (1 + float(sl_pct) / 100), 2)
            elif c.get("stop_loss") is not None and entry_max > 0:
                sl_pct = round((c["stop_loss"] / entry_max - 1) * 100, 2)
                c["stop_loss_pct"] = sl_pct
            tp_pct = c.get("take_profit_pct")
            if tp_pct is not None:
                if entry_max > 0:
                    c["take_profit"] = round(entry_max * (1 + float(tp_pct) / 100), 2)
            elif c.get("take_profit") is not None and entry_max > 0:
                tp_pct = round((c["take_profit"] / entry_max - 1) * 100, 2)
                c["take_profit_pct"] = tp_pct
            c.setdefault("cooldown_days", 1)
            c.setdefault("max_hold_days", 5)
            c.setdefault("expires_after_days", 2)
            c.setdefault("valid_until",
                (self._now + timedelta(days=int(c.get("expires_after_days", 2)))).strftime("%Y-%m-%d"))
            return self.normalize_candidate_strategy(c)

        new_codes = {c.get("code", "") for c in candidates}
        new_codes.discard("")
        merged = [
            c for c in self._data.get("buy_candidates", [])
            if c.get("valid_until", "") >= today and c.get("code", "") not in new_codes
        ]
        for c in candidates:
            if c.get("code"):
                merged.append(_normalize(c))

        self._data["buy_candidates"] = merged
        self.save("plan_stage")

    def mark_stopped_out(self, code: str, cooldown_hours: int = 24) -> None:
        until = (self._now + timedelta(hours=cooldown_hours)).isoformat()
        self._data["cooldown"][code] = until
        if code not in self._data["today_stopped_out"]:
            self._data["today_stopped_out"].append(code)
        self.save("stop_cooldown")

    def is_on_cooldown(self, code: str) -> bool:
        until_str = self._data.get("cooldown", {}).get(code)
        if not until_str:
            return False
        try:
            until = datetime.fromisoformat(until_str)
            return self._now < until
        except (ValueError, TypeError):
            return False

    def get_candidate(self, code: str) -> dict | None:
        for c in self._data.get("buy_candidates", []):
            if c.get("code") == code:
                return self.normalize_candidate_strategy(dict(c))
        return None

    @staticmethod
    def normalize_candidate_strategy(candidate: dict) -> dict:
        """Normalize candidate strategy type for paper-mode alpha routing.

        Paper mode defaults to automatic execution. Ambiguous entries become
        breakout candidates unless the published plan marks them watch_only.
        """
        c = dict(candidate)
        valid_types = {"breakout", "pullback", "defensive", "watch_only"}
        raw_type = str(c.get("strategy_type") or "").strip().lower()
        if raw_type in valid_types:
            strategy_type = raw_type
        else:
            reasoning = str(c.get("reasoning") or "")
            source = str(c.get("source") or "").strip().upper()
            volatility = c.get("volatility") or {}
            annualized_vol = float(volatility.get("annualized_volatility") or 0)
            vol_pct = float(volatility.get("volatility_percentile") or 0)

            pullback_words = ("回踩", "缩量", "低吸", "支撑", "ma5", "ma10", "MA5", "MA10")
            defensive_words = ("防御", "高股息", "红利", "银行", "煤炭", "电力", "低波动")
            breakout_words = ("涨停", "突破", "放量", "强势", "高换手", "首板")

            if c.get("pullback_zone") or c.get("support_price") or any(w in reasoning for w in pullback_words):
                strategy_type = "pullback"
            elif any(w in reasoning for w in defensive_words):
                strategy_type = "defensive"
            elif source == "B" or annualized_vol >= 0.55 or vol_pct >= 80 or any(w in reasoning for w in breakout_words):
                strategy_type = "breakout"
            else:
                strategy_type = "breakout"

        c["strategy_type"] = strategy_type
        if strategy_type == "breakout":
            c.setdefault("confirm_after", "09:45")
            c.setdefault("probe_position_pct", min(float(c.get("position_pct", 5) or 5), 5.0))
        return c

    def get_stopped_out_today(self) -> list[str]:
        return list(self._data.get("today_stopped_out", []))

    def clear_expired_cooldowns(self) -> None:
        now = self._now
        expired = []
        for code, until_str in list(self._data.get("cooldown", {}).items()):
            try:
                if now >= datetime.fromisoformat(until_str):
                    expired.append(code)
            except (ValueError, TypeError):
                expired.append(code)
        for code in expired:
            del self._data["cooldown"][code]
        if expired:
            self.save("cooldown_cleanup")

    def set_adjustments(self, adjustments: list[dict]) -> None:
        self._data["holding_adjustments"] = adjustments
        self.save("plan_stage")

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
