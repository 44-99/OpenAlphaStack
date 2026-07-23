from __future__ import annotations

import pandas as pd
import requests
import sys

from openalphastack.engine.data_feed import BacktestDataFeed


class _EmptyAkshare:
    @staticmethod
    def stock_zh_a_hist_min_em(**_kwargs):
        return pd.DataFrame()


def test_missing_intraday_data_is_not_synthesized_from_daily_ohlc(monkeypatch, tmp_path):
    feed = BacktestDataFeed("2026-01-05", "2026-01-05", ["600519"], 60)
    feed._minute_cache_dir = str(tmp_path)
    daily = pd.DataFrame(
        [{"date": pd.Timestamp("2026-01-05"), "open": 10, "high": 20, "low": 5, "close": 18, "volume": 1000}]
    )

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.ConnectionError()))
    monkeypatch.setattr(feed, "_safe_import_akshare", lambda: (_EmptyAkshare(), list(sys.path)))
    monkeypatch.setattr(feed, "_load_one_daily", lambda _code: daily)

    result = feed._load_one_minute("600519")

    assert result.empty
    assert feed._minute_cache["600519"].empty
