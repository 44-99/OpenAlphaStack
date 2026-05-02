"""Technical indicators computed from akshare historical data. No ta-lib required."""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import numpy as np
from _http import friendly_error  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
CACHE_TTL = 600


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"tech_{name}.json")


def _read_cache(name: str) -> dict | None:
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > CACHE_TTL:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_cache(name: str, data: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def fetch_hist(code: str, days: int = 120) -> pd.DataFrame:
    """Fetch daily historical OHLCV data."""
    import akshare as ak
    df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
    if df.empty:
        return df
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").tail(days)
    return df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "change_pct",
    })


def calc_ma(df: pd.DataFrame, periods: list[int] | None = None) -> dict:
    """Moving averages."""
    if periods is None:
        periods = [5, 10, 20, 60]
    result = {}
    close = df["close"].values
    for p in periods:
        if len(close) >= p:
            ma = pd.Series(close).rolling(p).mean().iloc[-1]
            result[f"MA{p}"] = round(float(ma), 2)
        else:
            result[f"MA{p}"] = None
    result["price"] = round(float(close[-1]), 2)
    result["vs_ma5"] = round((close[-1] / result["MA5"] - 1) * 100, 2) if result.get("MA5") else None
    result["vs_ma20"] = round((close[-1] / result["MA20"] - 1) * 100, 2) if result.get("MA20") else None
    return result


def calc_macd(df: pd.DataFrame) -> dict:
    """MACD (12, 26, 9)."""
    close = df["close"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    bar = 2 * (dif - dea)
    return {
        "DIF": round(float(dif.iloc[-1]), 4),
        "DEA": round(float(dea.iloc[-1]), 4),
        "BAR": round(float(bar.iloc[-1]), 4),
        "signal": "bullish" if dif.iloc[-1] > dea.iloc[-1] else "bearish",
        "crossover": "golden" if dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1] else (
            "death" if dif.iloc[-2] >= dea.iloc[-2] and dif.iloc[-1] < dea.iloc[-1] else "none"
        ),
    }


def calc_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    """RSI."""
    close = df["close"]
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    val = round(float(rsi.iloc[-1]), 2)
    zone = "oversold" if val < 30 else ("overbought" if val > 70 else "neutral")
    return {"RSI": val, "period": period, "zone": zone}


def calc_kdj(df: pd.DataFrame, n: int = 9) -> dict:
    """KDJ (9, 3, 3)."""
    low_list = df["low"].rolling(n).min()
    high_list = df["high"].rolling(n).max()
    close = df["close"]
    rsv = (close - low_list) / (high_list - low_list) * 100

    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d

    kj = round(float(k.iloc[-1]), 2)
    dj = round(float(d.iloc[-1]), 2)
    jj = round(float(j.iloc[-1]), 2)

    if kj < 20 and dj < 20:
        zone = "oversold"
    elif kj > 80 and dj > 80:
        zone = "overbought"
    else:
        zone = "neutral"

    return {"K": kj, "D": dj, "J": jj, "zone": zone}


def calc_bollinger(df: pd.DataFrame, period: int = 20) -> dict:
    """Bollinger Bands."""
    close = df["close"]
    ma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    price = float(close.iloc[-1])
    mid = round(float(ma.iloc[-1]), 2)
    up = round(float(upper.iloc[-1]), 2)
    lo = round(float(lower.iloc[-1]), 2)
    width = round((up - lo) / mid * 100, 2) if mid else None
    position = (
        "above_upper" if price > up else
        "below_lower" if price < lo else
        "inside"
    )
    return {
        "upper": up, "middle": mid, "lower": lo,
        "width_pct": width, "price_position": position,
    }


def calc_volume_price(df: pd.DataFrame) -> dict:
    """Volume-price relationship analysis."""
    recent = df.tail(20)
    vol = recent["volume"].values
    price = recent["close"].values
    avg_vol = float(np.mean(vol))
    latest_vol = float(vol[-1])
    vol_ratio = round(latest_vol / avg_vol, 2) if avg_vol > 0 else 1.0

    price_up = price[-1] > price[-6] if len(price) >= 6 else False
    vol_up = latest_vol > avg_vol * 1.2

    if price_up and vol_up:
        signal = "accumulation"
    elif not price_up and vol_up:
        signal = "distribution"
    elif price_up and not vol_up:
        signal = "divergence_bearish"
    elif not price_up and not vol_up:
        signal = "quiet"
    else:
        signal = "neutral"

    return {
        "avg_volume_20d": round(avg_vol, 0),
        "latest_volume": round(latest_vol, 0),
        "volume_ratio": vol_ratio,
        "signal": signal,
    }


def main():
    parser = argparse.ArgumentParser(description="Technical indicators for A-share stocks")
    parser.add_argument("code", help="Stock code (6 digits)")
    parser.add_argument("--indicator", "-i",
                        choices=["ma", "macd", "rsi", "kdj", "bollinger", "volume", "all"],
                        default="all", help="Which indicator to compute")
    args = parser.parse_args()

    try:
        code = args.code
        cache_key = f"{code}_{args.indicator}"
        cached = _read_cache(cache_key)
        if cached:
            print(json.dumps(cached, ensure_ascii=False, indent=2, default=str))
            return

        df = fetch_hist(code)
        if df.empty:
            print(json.dumps({"error": f"No historical data for {code}"}, ensure_ascii=False))
            sys.exit(1)

        name = str(df["name"].iloc[-1]) if "name" in df.columns else code
        result = {"code": code, "name": name, "time": datetime.now().strftime("%Y-%m-%d %H:%M")}

        indicator = args.indicator
        if indicator in ("ma", "all"):
            result["ma"] = calc_ma(df)
        if indicator in ("macd", "all"):
            result["macd"] = calc_macd(df)
        if indicator in ("rsi", "all"):
            result["rsi"] = calc_rsi(df)
        if indicator in ("kdj", "all"):
            result["kdj"] = calc_kdj(df)
        if indicator in ("bollinger", "all"):
            result["bollinger"] = calc_bollinger(df)
        if indicator in ("volume", "all"):
            result["volume_price"] = calc_volume_price(df)

        _write_cache(cache_key, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    except Exception as e:
        print(json.dumps({"error": friendly_error(args.code, e), "code": args.code}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
