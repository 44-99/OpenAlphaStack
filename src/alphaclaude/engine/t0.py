"""T+0 intraday state helpers."""

from __future__ import annotations

from alphaclaude.engine.state import round_lot


class T0Tracker:
    """Per-stock intraday T+0 state machine."""

    __slots__ = (
        "code", "enabled", "preferred_direction", "max_shares_pct",
        "max_shares", "buy_trigger_price", "sell_target_pct",
        "stop_loss_pct", "max_rounds", "breakout_price",
        "breakdown_price", "atr_pct",
        "rounds_done", "state", "t0_shares", "t0_entry_price",
        "t0_stop_price", "t0_target_price", "paused_until",
    )

    def __init__(self, code: str):
        self.code = code
        self.enabled = False
        self.preferred_direction = "forward"
        self.max_shares_pct = 30.0
        self.max_shares = 0
        self.buy_trigger_price = 0.0
        self.sell_target_pct = 2.0
        self.stop_loss_pct = -1.5
        self.max_rounds = 2
        self.breakout_price = 0.0
        self.breakdown_price = 0.0
        self.atr_pct = 3.0
        self.rounds_done = 0
        self.state = "idle"
        self.t0_shares = 0
        self.t0_entry_price = 0.0
        self.t0_stop_price = 0.0
        self.t0_target_price = 0.0
        self.paused_until = ""

    def load_config(self, cfg: dict, available_shares: int) -> None:
        """Apply t0_config from plan.json and compute derived values."""
        self.enabled = bool(cfg.get("enabled", False))
        if not self.enabled:
            return
        self.preferred_direction = cfg.get("preferred_direction", "forward")
        self.max_shares_pct = float(cfg.get("max_shares_pct", 30))
        self.buy_trigger_price = float(cfg.get("buy_trigger_price", 0))
        self.sell_target_pct = float(cfg.get("sell_target_pct", 2.0))
        self.stop_loss_pct = float(cfg.get("stop_loss_pct", -1.5))
        self.max_rounds = int(cfg.get("max_rounds", 2))
        self.breakout_price = float(cfg.get("breakout_price", 0))
        self.breakdown_price = float(cfg.get("breakdown_price", 0))
        self.atr_pct = float(cfg.get("atr_pct", 3.0))

        self.max_shares = round_lot(int(available_shares * self.max_shares_pct / 100.0))

    def reset_day(self) -> None:
        self.rounds_done = 0
        self.state = "idle"
        self.t0_shares = 0
        self.t0_entry_price = 0.0
        self.t0_stop_price = 0.0
        self.t0_target_price = 0.0
        self.paused_until = ""
