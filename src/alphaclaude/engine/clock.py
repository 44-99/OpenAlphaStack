"""Trading session clock for real-time and backtest modes."""

from __future__ import annotations

from datetime import datetime, timedelta

from alphaclaude.engine.constants import (
    AFTERNOON_END,
    AFTERNOON_START,
    AUCTION_END,
    AUCTION_START,
    MORNING_END,
    MORNING_START,
    PRE_MARKET_START,
)


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
        while self.sim_time.weekday() >= 5:
            self.sim_time += timedelta(days=1)
        if t < AUCTION_START and self.sim_time.time() >= AUCTION_START:
            pass
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
