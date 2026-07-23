"""Backtest data feed and historical bar helpers."""

from __future__ import annotations

import json
import os
import sys
import threading

import pandas as pd

from openalphastack.paths import DATA_DIR, add_legacy_paths


def _generate_day_bars(date: pd.Timestamp, period: int = 60) -> list[pd.Timestamp]:
    """Generate bar timestamps for a trading day at given period in minutes."""
    bars = []
    morning_open = date.replace(hour=9, minute=30)
    morning_close = date.replace(hour=11, minute=30)
    afternoon_open = date.replace(hour=13, minute=0)
    afternoon_close = date.replace(hour=15, minute=0)

    t = morning_open + pd.Timedelta(minutes=period)
    while t <= morning_close:
        bars.append(t)
        t += pd.Timedelta(minutes=period)

    t = afternoon_open + pd.Timedelta(minutes=period)
    while t <= afternoon_close:
        bars.append(t)
        t += pd.Timedelta(minutes=period)

    return bars


class BacktestDataFeed:
    """Lazy historical data feed that mirrors paper mode's on-demand pattern."""

    def __init__(self, start_date: str, end_date: str,
                 universe: list[str], bar_period: int = 60):
        self.universe = universe
        self.start = pd.Timestamp(start_date)
        self.end = pd.Timestamp(end_date)
        self.bar_period = bar_period
        self._daily_cache: dict[str, pd.DataFrame] = {}
        self._minute_cache: dict[str, pd.DataFrame] = {}
        self._bars_cache: dict[str, pd.DataFrame] = {}
        self._load_lock = threading.Lock()
        self._index_cache: pd.DataFrame | None = None
        self._index_loaded = False
        self._minute_cache_dir = str(DATA_DIR / "cache" / "minute")
        os.makedirs(self._minute_cache_dir, exist_ok=True)
        print(f"[DataFeed] Lazy mode. {len(universe)} stocks in universe, "
              f"{bar_period}m bars. Data loaded on-demand per monitored stock.")

    @staticmethod
    def _safe_import_akshare():
        """Import akshare safely despite tools/signal.py shadowing stdlib signal."""
        _saved_path = list(sys.path)
        sys.path = [p for p in sys.path if os.path.basename(p.rstrip('/\\')) != 'tools']
        sys.modules.pop('signal', None)
        import signal  # noqa: F401
        try:
            import akshare as ak
            return ak, _saved_path
        except Exception:
            sys.path[:] = _saved_path
            raise

    def _load_one_daily(self, code: str) -> pd.DataFrame:
        """Lazy-load daily OHLC for one stock. Blocking on first call, cached thereafter."""
        if code in self._daily_cache:
            return self._daily_cache[code]
        add_legacy_paths()
        from openalphastack.tools._fallback import get_hist
        try:
            df, source = get_hist(code, days=1500)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
                df = df[df["date"] <= self.end]
                self._daily_cache[code] = df
                return df
            print(f"[DataFeed] Daily data empty for {code} (source={source})")
        except Exception as e:
            print(f"[DataFeed] Daily data load failed for {code}: {type(e).__name__}: {e}")
        self._daily_cache[code] = pd.DataFrame()
        return pd.DataFrame()

    @staticmethod
    def _sina_code(code: str) -> str:
        """Convert code to Sina/Tencent prefix format: sh600000 / sz000001."""
        return f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"

    def _load_one_minute(self, code: str) -> pd.DataFrame:
        """Lazy-load intraday bars for one stock."""
        if code in self._minute_cache:
            return self._minute_cache[code]

        period = self.bar_period
        cache_path = os.path.join(self._minute_cache_dir, f"{code}_{period}m.parquet")
        sina = self._sina_code(code)

        try:
            if os.path.exists(cache_path):
                df = pd.read_parquet(cache_path)
                if not df.empty and "time" in df.columns:
                    df["time"] = pd.to_datetime(df["time"])
                    mask = (df["time"] >= self.start) & (df["time"] <= self.end + pd.Timedelta(days=1))
                    df = df[mask]
                    if not df.empty:
                        self._minute_cache[code] = df
                        return df
        except Exception as e:
            print(f"[DataFeed] Parquet cache read failed for {code}: {e}")

        df = None

        def _try_accept(full_df, src_name):
            if full_df is None or full_df.empty:
                return None
            try:
                full_df.to_parquet(cache_path, index=False)
            except Exception as e:
                print(f"[DataFeed] Parquet cache write failed for {code}: {e}")
            mask = (full_df["time"] >= self.start) & (full_df["time"] <= self.end + pd.Timedelta(days=1))
            windowed = full_df[mask]
            if not windowed.empty:
                return windowed
            return None

        tx_period = f"m{period}"

        try:
            import requests as _req
            url = f"http://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline?param={sina},{tx_period},,1500"
            resp = _req.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(resp.text)
            bars = data.get("data", {}).get(sina, {}).get(tx_period, [])
            if bars:
                rows = []
                for b in bars:
                    rows.append({
                        "time": pd.Timestamp(b[0]),
                        "open": float(b[1]),
                        "close": float(b[2]),
                        "high": float(b[3]),
                        "low": float(b[4]),
                        "volume": int(float(b[5])),
                        "amount": 0,
                    })
                raw = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
                df = _try_accept(raw, "tencent")
        except Exception as e:
            print(f"[DataFeed] Tencent {tx_period} failed for {code}: {type(e).__name__}: {e}")

        if df is None:
            try:
                import requests as _req
                url = (
                    f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                    f"CN_MarketData.getKLineData?symbol={sina}&scale={period}&ma=no&datalen=1500"
                )
                resp = _req.get(url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn/",
                })
                data = json.loads(resp.text)
                if data and isinstance(data, list):
                    rows = []
                    for d in data:
                        rows.append({
                            "time": pd.Timestamp(d["day"]),
                            "open": float(d["open"]),
                            "high": float(d["high"]),
                            "low": float(d["low"]),
                            "close": float(d["close"]),
                            "volume": int(float(d["volume"])),
                            "amount": 0,
                        })
                    raw = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
                    df = _try_accept(raw, "sina")
            except Exception as e:
                print(f"[DataFeed] Sina scale={period} failed for {code}: {type(e).__name__}: {e}")

        if df is None:
            ak, _saved_path = self._safe_import_akshare()
            try:
                raw = ak.stock_zh_a_hist_min_em(symbol=code, period=str(period), adjust="qfq")
                if raw is not None and not raw.empty:
                    raw = raw.rename(columns={
                        "时间": "time", "开盘": "open", "收盘": "close",
                        "最高": "high", "最低": "low", "成交量": "volume",
                        "成交额": "amount",
                    })
                    raw["time"] = pd.to_datetime(raw["time"])
                    raw = raw.sort_values("time").reset_index(drop=True)
                    df = _try_accept(raw, "akshare")
            except Exception as e:
                print(f"[DataFeed] akshare minute failed for {code}: {type(e).__name__}: {e}")
            finally:
                sys.path[:] = _saved_path

        if df is not None and not df.empty:
            self._minute_cache[code] = df
            return df

        print(f"[DataFeed] {code}: NO real {period}m data available — quotes will be empty")
        self._minute_cache[code] = pd.DataFrame()
        return pd.DataFrame()

    def _load_bars(self, code: str, period: int) -> pd.DataFrame:
        """Load intraday bars at any period."""
        cache_key = f"{code}_{period}m"
        if cache_key in self._bars_cache:
            return self._bars_cache[cache_key]

        cache_path = os.path.join(self._minute_cache_dir, cache_key + ".parquet")
        sina = self._sina_code(code)

        try:
            if os.path.exists(cache_path):
                df = pd.read_parquet(cache_path)
                if not df.empty and "time" in df.columns:
                    df["time"] = pd.to_datetime(df["time"])
                    mask = (df["time"] >= self.start) & (df["time"] <= self.end + pd.Timedelta(days=1))
                    df = df[mask]
                    if not df.empty:
                        self._bars_cache[cache_key] = df
                        return df
        except Exception as e:
            print(f"[DataFeed] Parquet cache read failed for {code} {period}m: {e}")

        df = None

        def _try_accept(full_df, src_name):
            if full_df is None or full_df.empty:
                return None
            try:
                full_df.to_parquet(cache_path, index=False)
            except Exception as e:
                print(f"[DataFeed] Parquet cache write failed for {code} {period}m: {e}")
            mask = (full_df["time"] >= self.start) & (full_df["time"] <= self.end + pd.Timedelta(days=1))
            windowed = full_df[mask]
            return windowed if not windowed.empty else None

        tx_period = f"m{period}"
        try:
            import requests as _req
            url = f"http://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline?param={sina},{tx_period},,1500"
            resp = _req.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(resp.text)
            bars = data.get("data", {}).get(sina, {}).get(tx_period, [])
            if bars:
                rows = []
                for b in bars:
                    rows.append({
                        "time": pd.Timestamp(b[0]),
                        "open": float(b[1]),
                        "close": float(b[2]),
                        "high": float(b[3]),
                        "low": float(b[4]),
                        "volume": int(float(b[5])),
                        "amount": 0,
                    })
                raw = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
                df = _try_accept(raw, "tencent")
        except Exception as e:
            print(f"[DataFeed] Tencent m{period} failed for {code}: {type(e).__name__}: {e}")

        if df is None and period != 1:
            try:
                import requests as _req
                url = (
                    f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                    f"CN_MarketData.getKLineData?symbol={sina}&scale={period}&ma=no&datalen=1500"
                )
                resp = _req.get(url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://finance.sina.com.cn/",
                })
                data = json.loads(resp.text)
                if data and isinstance(data, list):
                    rows = []
                    for d in data:
                        rows.append({
                            "time": pd.Timestamp(d["day"]),
                            "open": float(d["open"]),
                            "high": float(d["high"]),
                            "low": float(d["low"]),
                            "close": float(d["close"]),
                            "volume": int(float(d["volume"])),
                            "amount": 0,
                        })
                    raw = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
                    df = _try_accept(raw, "sina")
            except Exception as e:
                print(f"[DataFeed] Sina scale={period} failed for {code}: {type(e).__name__}: {e}")

        if df is None:
            ak, _saved_path = self._safe_import_akshare()
            try:
                raw = ak.stock_zh_a_hist_min_em(symbol=code, period=str(period), adjust="qfq")
                if raw is not None and not raw.empty:
                    raw = raw.rename(columns={
                        "时间": "time", "开盘": "open", "收盘": "close",
                        "最高": "high", "最低": "low", "成交量": "volume",
                        "成交额": "amount",
                    })
                    raw["time"] = pd.to_datetime(raw["time"])
                    raw = raw.sort_values("time").reset_index(drop=True)
                    df = _try_accept(raw, "akshare")
            except Exception as e:
                print(f"[DataFeed] akshare m{period} failed for {code}: {type(e).__name__}: {e}")
            finally:
                sys.path[:] = _saved_path

        if df is not None and not df.empty:
            self._bars_cache[cache_key] = df
            return df

        self._bars_cache[cache_key] = pd.DataFrame()
        return pd.DataFrame()

    def load_bars_window(self, code: str, period: int,
                         start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
        """Load bars at a specific resolution for a specific time window."""
        df = self._load_bars(code, period)
        if df is None or df.empty:
            return pd.DataFrame()
        mask = (df["time"] > start_ts) & (df["time"] <= end_ts)
        return df[mask].sort_values("time").reset_index(drop=True)

    def refine_entry(self, code: str, bar_ts: pd.Timestamp,
                     entry_min: float, entry_max: float) -> tuple | None:
        """Progressive hierarchical zoom for precise entry price."""
        from_period = self.bar_period

        if from_period <= 1:
            refine_chain = []
        elif from_period <= 5:
            refine_chain = [1]
        else:
            refine_chain = [5, 1]

        if not refine_chain:
            return None

        current_start = bar_ts - pd.Timedelta(minutes=from_period)
        current_end = bar_ts
        best_price = None
        best_resolution = from_period
        best_time = bar_ts
        entry_min_safe = max(entry_min, 0.01)

        for period in refine_chain:
            df = self.load_bars_window(code, period, current_start, current_end)
            if df is None or df.empty:
                continue

            found = False
            for _, row in df.iterrows():
                bar_low = float(row["low"])
                bar_high = float(row["high"])
                if bar_low <= entry_max and bar_high >= entry_min_safe:
                    found = True
                    bar_open = float(row["open"])
                    if bar_open > entry_max:
                        best_price = entry_max
                    elif bar_open < entry_min_safe:
                        best_price = entry_min_safe
                    else:
                        best_price = bar_open
                    best_resolution = period
                    best_time = row["time"]
                    current_start = row["time"] - pd.Timedelta(minutes=period)
                    current_end = row["time"]
                    break

            if not found:
                break

        if best_price is None:
            return None
        return best_price, best_resolution, best_time

    def _ensure_index(self) -> None:
        """Lazy-load Shanghai Composite index data."""
        if self._index_loaded:
            return
        ak, _saved_path = self._safe_import_akshare()
        try:
            idx_df = ak.stock_zh_index_daily(symbol="sh000001")
            if not idx_df.empty:
                col_map = {
                    "日期": "date", "开盘": "open", "收盘": "close",
                    "最高": "high", "最低": "low", "成交量": "volume",
                }
                rename_map = {k: v for k, v in col_map.items() if k in idx_df.columns}
                if rename_map:
                    idx_df = idx_df.rename(columns=rename_map)
                idx_df["date"] = pd.to_datetime(idx_df["date"])
                idx_df = idx_df.sort_values("date").reset_index(drop=True)
                idx_df = idx_df[idx_df["date"] <= self.end]
                self._index_cache = idx_df
        except Exception as e:
            import traceback
            print(f"[DataFeed] Index load failed: {e}")
            traceback.print_exc()
            self._index_cache = pd.DataFrame()
        finally:
            sys.path[:] = _saved_path
        self._index_loaded = True

    def _ensure_loaded(self, code: str) -> None:
        """Ensure daily data is loaded."""
        if code in self._daily_cache:
            return
        self._load_one_daily(code)

    def get_minute_quote(self, code: str, minute_ts: pd.Timestamp) -> dict:
        """Return quote dict for a single stock at a specific bar."""
        self._load_one_minute(code)
        df = self._minute_cache.get(code)
        if df is None or df.empty:
            return {}
        row = df[df["time"] == minute_ts]
        if row.empty:
            return {}
        row = row.iloc[-1]

        self._load_one_daily(code)
        daily = self._daily_cache.get(code)
        prev_close = float(row["open"])
        if daily is not None and not daily.empty:
            prev_daily = daily[daily["date"] < pd.Timestamp(minute_ts.date())]
            if not prev_daily.empty:
                prev_close = float(prev_daily.iloc[-1]["close"])

        vol_ratio = 1.0
        if daily is not None and not daily.empty:
            prev_5 = daily[daily["date"] < pd.Timestamp(minute_ts.date())].tail(5)
            if not prev_5.empty:
                avg_vol = prev_5["volume"].mean()
                if avg_vol > 0:
                    same_day = df[df["time"].dt.date == minute_ts.date()]
                    cum_vol = same_day[same_day["time"] <= minute_ts]["volume"].sum()
                    vol_ratio = round(cum_vol / avg_vol, 2)

        return {
            "code": code,
            "price": float(row["close"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "prev_close": prev_close,
            "volume": int(row.get("volume", 0)),
            "volume_ratio": vol_ratio,
            "change_pct": round(
                (float(row["close"]) - prev_close) / prev_close * 100, 2
            ) if prev_close else 0,
        }

    def get_index_quote(self, date: pd.Timestamp) -> dict:
        """Return market index quote for a trading day."""
        self._ensure_index()
        if self._index_cache is None or self._index_cache.empty:
            return {}
        row = self._index_cache[self._index_cache["date"] == date]
        if row.empty:
            return {}
        row = row.iloc[-1]
        prev_idx = self._index_cache[self._index_cache["date"] < date]
        prev = float(prev_idx.iloc[-1]["close"]) if not prev_idx.empty else float(row["open"])
        return {
            "code": "000001",
            "price": float(row["close"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "prev_close": prev,
            "volume": int(row.get("volume", 0)),
            "change_pct": round((float(row["close"]) - prev) / prev * 100, 2) if prev else 0,
        }

    def get_day_bars(self, date: pd.Timestamp) -> list[pd.Timestamp]:
        """Return bar timestamps for a trading day at self.bar_period."""
        return _generate_day_bars(date, self.bar_period)

    def get_history_up_to(self, code: str, date: pd.Timestamp,
                          days: int = 120) -> pd.DataFrame:
        """Return historical daily DataFrame for `code` up to `date`."""
        self._ensure_loaded(code)
        df = self._daily_cache.get(code)
        if df is None or df.empty:
            return pd.DataFrame()
        mask = df["date"] <= date
        return df[mask].tail(days).reset_index(drop=True)

    def current_day_data(self, date: pd.Timestamp) -> dict[str, dict]:
        """Get loaded stocks' daily data for a specific date."""
        quotes = {}
        for code in list(self._daily_cache.keys()):
            df = self._daily_cache[code]
            if df.empty:
                continue
            row = df[df["date"] == date]
            if row.empty:
                continue
            row = row.iloc[-1]
            prev_rows = df[df["date"] < date]
            prev_close = float(prev_rows.iloc[-1]["close"]) if not prev_rows.empty else float(row["open"])
            vol_5d = prev_rows.tail(5)["volume"].mean() if not prev_rows.empty else float(row["volume"])
            vol_ratio = float(row["volume"]) / vol_5d if vol_5d and vol_5d > 0 else 1.0
            quotes[code] = {
                "code": code,
                "price": float(row["close"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "prev_close": prev_close,
                "volume": int(row["volume"]),
                "volume_ratio": round(vol_ratio, 2),
                "change_pct": round(
                    (float(row["close"]) - prev_close) / prev_close * 100, 2
                ) if prev_close else 0,
            }
        index_q = self.get_index_quote(date)
        if index_q:
            quotes["000001"] = index_q
        return quotes

    def trading_days(self) -> list[pd.Timestamp]:
        """All unique trading days from index cache and loaded daily data."""
        self._ensure_index()
        all_dates = set()
        if self._index_cache is not None and not self._index_cache.empty:
            all_dates.update(self._index_cache["date"].tolist())
        for df in list(self._daily_cache.values()):
            if not df.empty:
                all_dates.update(df["date"].tolist())
        return sorted([d for d in all_dates
                       if self.start <= d <= self.end])

    def previous_trading_day(self, date: pd.Timestamp) -> pd.Timestamp | None:
        """Return the trading day immediately before `date`, or None."""
        self._ensure_index()
        if self._index_cache is not None and not self._index_cache.empty:
            prev = self._index_cache[self._index_cache["date"] < date]
            if not prev.empty:
                return prev["date"].max()
        return None
