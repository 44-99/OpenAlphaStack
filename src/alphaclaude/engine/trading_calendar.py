"""A-share trading day helpers."""

from __future__ import annotations

from datetime import date
from functools import lru_cache


@lru_cache(maxsize=32)
def is_trading_day(day: date) -> bool:
    """Return whether the A-share market is expected to trade on this date."""
    if day.weekday() >= 5:
        return False

    try:
        import akshare as ak
        import pandas as pd

        calendar = ak.tool_trade_date_hist_sina()
        if calendar.empty:
            return True
        column = "trade_date" if "trade_date" in calendar.columns else calendar.columns[0]
        dates = set(pd.to_datetime(calendar[column]).dt.date)
        if not dates:
            return True
        return day in dates
    except Exception:
        # Fail open on weekdays so a market-calendar outage does not halt trading.
        return True


def non_trading_reason(day: date) -> str:
    """Human-readable closed-market reason."""
    if day.weekday() == 5:
        return "周六休市"
    if day.weekday() == 6:
        return "周日休市"
    return "交易所休市"
