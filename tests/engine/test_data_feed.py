from __future__ import annotations

import pandas as pd

from openalphastack.engine import BacktestDataFeed
from openalphastack.engine.data_feed import _generate_day_bars


def test_generate_day_bars_respects_a_share_sessions():
    bars = _generate_day_bars(pd.Timestamp("2025-03-14"), period=60)

    assert [b.strftime("%H:%M") for b in bars] == ["10:30", "11:30", "14:00", "15:00"]


def test_minute_quote_uses_real_cached_intraday_and_previous_close():
    feed = BacktestDataFeed("2025-03-13", "2025-03-14", ["600036"], bar_period=60)
    feed._index_loaded = True
    feed._daily_cache["600036"] = pd.DataFrame([
        {
            "date": pd.Timestamp("2025-03-13"),
            "open": 40.0,
            "high": 42.0,
            "low": 39.5,
            "close": 41.0,
            "volume": 1000,
            "amount": 10000.0,
        },
        {
            "date": pd.Timestamp("2025-03-14"),
            "open": 41.5,
            "high": 43.0,
            "low": 41.0,
            "close": 42.5,
            "volume": 2000,
            "amount": 20000.0,
        },
    ])
    feed._minute_cache["600036"] = pd.DataFrame([
        {
            "time": pd.Timestamp("2025-03-14 10:30"),
            "open": 41.5,
            "high": 42.0,
            "low": 41.2,
            "close": 41.8,
            "volume": 500,
            "amount": 5000.0,
        }
    ])

    quote = feed.get_minute_quote("600036", pd.Timestamp("2025-03-14 10:30"))

    assert quote["code"] == "600036"
    assert quote["price"] == 41.8
    assert quote["prev_close"] == 41.0
    assert quote["change_pct"] == 1.95
    assert quote["volume_ratio"] == 0.5


def test_trading_days_and_previous_day_can_use_loaded_index_cache():
    feed = BacktestDataFeed("2025-03-13", "2025-03-14", ["600036"], bar_period=60)
    feed._index_loaded = True
    feed._index_cache = pd.DataFrame([
        {"date": pd.Timestamp("2025-03-12"), "open": 3190.0, "high": 3220.0, "low": 3180.0, "close": 3200.0, "volume": 100},
        {"date": pd.Timestamp("2025-03-13"), "open": 3200.0, "high": 3230.0, "low": 3190.0, "close": 3220.0, "volume": 120},
        {"date": pd.Timestamp("2025-03-14"), "open": 3220.0, "high": 3240.0, "low": 3210.0, "close": 3230.0, "volume": 130},
    ])

    assert feed.trading_days() == [pd.Timestamp("2025-03-13"), pd.Timestamp("2025-03-14")]
    assert feed.previous_trading_day(pd.Timestamp("2025-03-14")) == pd.Timestamp("2025-03-13")

    index_quote = feed.get_index_quote(pd.Timestamp("2025-03-14"))

    assert index_quote["code"] == "000001"
    assert index_quote["price"] == 3230.0
    assert index_quote["prev_close"] == 3220.0
