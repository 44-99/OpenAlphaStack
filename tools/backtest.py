"""Lightweight historical backtest for A-share strategies."""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from _http import friendly_error  # noqa: E402

import pandas as pd
import numpy as np

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
CACHE_TTL = 3600


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"bt_{name}.json")


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


def _sina_code(code: str) -> str:
    return f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"


def fetch_hist(code: str, days: int = 500) -> pd.DataFrame:
    """Fetch historical daily OHLCV data from Sina Finance."""
    import requests
    url = (
        f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={_sina_code(code)}&scale=240&ma=no&datalen={days}"
    )
    resp = requests.get(url, timeout=15,
                        headers={"User-Agent": "Mozilla/5.0",
                                 "Referer": "https://finance.sina.com.cn/"})
    resp.encoding = "gbk"
    data = json.loads(resp.text)
    if not data or not isinstance(data, list):
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df = df.rename(columns={"day": "date"})
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    return df.sort_values("date").tail(days).reset_index(drop=True)


def backtest_ma_cross(df: pd.DataFrame) -> dict:
    """Backtest MA5/MA20 golden/death cross strategy."""
    close = df["close"]
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()

    position = 0  # 0 = cash, 1 = holding
    trades = []
    entry_price = 0

    for i in range(20, len(df)):
        golden = ma5.iloc[i - 1] <= ma20.iloc[i - 1] and ma5.iloc[i] > ma20.iloc[i]
        death = ma5.iloc[i - 1] >= ma20.iloc[i - 1] and ma5.iloc[i] < ma20.iloc[i]

        if golden and position == 0:
            entry_price = close.iloc[i]
            position = 1
            trades.append({
                "date": str(df["date"].iloc[i])[:10],
                "action": "buy",
                "price": round(float(entry_price), 2),
            })
        elif death and position == 1:
            exit_price = close.iloc[i]
            pnl = round((exit_price - entry_price) / entry_price * 100, 2)
            position = 0
            trades.append({
                "date": str(df["date"].iloc[i])[:10],
                "action": "sell",
                "price": round(float(exit_price), 2),
                "pnl_pct": pnl,
            })

    # Close any open position at end
    if position == 1:
        exit_price = close.iloc[-1]
        pnl = round((exit_price - entry_price) / entry_price * 100, 2)
        trades.append({
            "date": str(df["date"].iloc[-1])[:10],
            "action": "sell",
            "price": round(float(exit_price), 2),
            "pnl_pct": pnl,
        })

    sell_trades = [t for t in trades if t["action"] == "sell"]
    returns = [t["pnl_pct"] for t in sell_trades]
    wins = len([r for r in returns if r > 0])
    losses = len([r for r in returns if r <= 0])

    result = {
        "strategy": "ma_cross",
        "description": "MA5/MA20 golden cross buy, death cross sell",
        "total_trades": len(sell_trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(sell_trades) * 100, 1) if sell_trades else 0,
        "avg_return": round(np.mean(returns), 2) if returns else 0,
        "max_return": round(max(returns), 2) if returns else 0,
        "min_return": round(min(returns), 2) if returns else 0,
        "total_return": round(sum(returns), 2),
        "recent_trades": trades[-10:],
    }
    return result


def backtest_volume_breakout(df: pd.DataFrame) -> dict:
    """Backtest volume breakout: volume > 1.5x avg + price up > 2%, hold 3 days."""
    close = df["close"]
    volume = df["volume"]
    avg_vol = volume.rolling(20).mean()
    trades = []

    for i in range(20, len(df) - 3):
        vol_ratio = volume.iloc[i] / avg_vol.iloc[i] if avg_vol.iloc[i] > 0 else 0
        price_change = (close.iloc[i] - close.iloc[i - 1]) / close.iloc[i - 1] * 100

        if vol_ratio > 1.5 and price_change > 2:
            entry = close.iloc[i]
            exit_p = close.iloc[i + 3]
            pnl = round((exit_p - entry) / entry * 100, 2)
            trades.append({
                "entry_date": str(df["date"].iloc[i])[:10],
                "exit_date": str(df["date"].iloc[i + 3])[:10],
                "entry_price": round(float(entry), 2),
                "exit_price": round(float(exit_p), 2),
                "pnl_pct": pnl,
            })

    returns = [t["pnl_pct"] for t in trades]
    wins = len([r for r in returns if r > 0])
    losses = len([r for r in returns if r <= 0])

    result = {
        "strategy": "volume_breakout",
        "description": "Volume > 1.5x avg + price up > 2%, hold 3 days",
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(trades) * 100, 1) if trades else 0,
        "avg_return": round(np.mean(returns), 2) if returns else 0,
        "max_return": round(max(returns), 2) if returns else 0,
        "min_return": round(min(returns), 2) if returns else 0,
        "total_return": round(sum(returns), 2),
        "recent_trades": trades[-10:],
    }
    return result


STRATEGIES = {
    "ma_cross": backtest_ma_cross,
    "volume_breakout": backtest_volume_breakout,
}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Historical backtest for A-share strategies")
    parser.add_argument("code", nargs="?", help="Stock code (6 digits)")
    parser.add_argument("--strategy", "-s", choices=list(STRATEGIES.keys()),
                        default="ma_cross", help="Strategy to backtest")
    parser.add_argument("--list", "-l", action="store_true", help="List available strategies")
    args = parser.parse_args()

    if args.list:
        info = {k: STRATEGIES[k].__doc__ for k in STRATEGIES}
        print(json.dumps({"strategies": info}, ensure_ascii=False, indent=2))
        return

    cache_key = f"{args.code}_{args.strategy}"
    cached = _read_cache(cache_key)
    if cached:
        print(json.dumps(cached, ensure_ascii=False, indent=2, default=str))
        if "error" in cached:
            sys.exit(1)
        return

    try:
        df = fetch_hist(args.code)
        if df.empty:
            result = {"error": f"No historical data for {args.code}", "code": args.code}
        else:
            name = str(df["name"].iloc[-1]) if "name" in df.columns else args.code
            fn = STRATEGIES[args.strategy]
            result = fn(df)
            result["code"] = args.code
            result["name"] = name
            result["period"] = f"{str(df['date'].iloc[0])[:10]} to {str(df['date'].iloc[-1])[:10]}"
            result["time"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        result = {"error": friendly_error(args.code, e), "code": args.code}

    _write_cache(cache_key, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
