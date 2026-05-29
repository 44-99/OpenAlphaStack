"""Rule-based signal engine — pure Python, zero LLM.
Runs in the package engine fast lane. Each rule returns dict or None.
Importable by the package engine, also usable as standalone CLI.
"""
import argparse
import json
import sys

import pandas as pd
from alphaclaude.tools._fallback import get_hist, get_quote


def _ma(series, period):
    """Simple moving average for a list or Series. Returns a list."""
    if hasattr(series, "rolling"):
        return series.rolling(period).mean().tolist()
    return [sum(series[max(0, i - period + 1):i + 1]) / min(i + 1, period)
            for i in range(len(series))]


def check_ma_cross(df: pd.DataFrame) -> dict | None:
    """Detect MA5/MA10 golden/death cross in last 3 trading days."""
    if len(df) < 15:
        return None
    closes = df["close"]
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)

    for days_ago in range(3):
        i = -(days_ago + 1)
        j = i - 1
        if ma5[i] > ma10[i] and ma5[j] <= ma10[j]:
            return {
                "rule": "ma_golden_cross",
                "action": "buy",
                "confidence": 75,
                "days_ago": days_ago,
                "ma5": round(float(ma5[i]), 2),
                "ma10": round(float(ma10[i]), 2),
                "price": round(float(closes.iloc[i]), 2),
                "suggested_stop": round(float(closes.iloc[i]) * 0.97, 2),
            }
        if ma5[i] < ma10[i] and ma5[j] >= ma10[j]:
            return {
                "rule": "ma_death_cross",
                "action": "sell",
                "confidence": 75,
                "days_ago": days_ago,
                "ma5": round(float(ma5[i]), 2),
                "ma10": round(float(ma10[i]), 2),
                "price": round(float(closes.iloc[i]), 2),
            }
    return None


def check_volume_breakout(df: pd.DataFrame, quote: dict = None) -> dict | None:
    """Volume > 1.5x 20d avg AND price change > 2% vs prev close."""
    if len(df) < 25:
        return None
    closes = df["close"]
    volumes = df["volume"]
    avg_vol_20 = volumes.iloc[-21:-1].mean()
    latest_vol = volumes.iloc[-1]
    vol_ratio = latest_vol / avg_vol_20 if avg_vol_20 > 0 else 0

    if vol_ratio < 1.5:
        return None

    price_change = (closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] * 100
    if price_change < 2:
        return None

    # Check if price is in upper half of daily range
    high = float(df.iloc[-1]["high"])
    low = float(df.iloc[-1]["low"])
    price = float(closes.iloc[-1])
    range_pos = (price - low) / (high - low) * 100 if high > low else 50

    return {
        "rule": "volume_breakout",
        "action": "buy",
        "confidence": 70,
        "price": round(price, 2),
        "vol_ratio": round(vol_ratio, 2),
        "price_change_pct": round(price_change, 2),
        "range_position_pct": round(range_pos, 1),
        "suggested_stop": round(price * 0.95, 2),
    }


def check_deviation_alert(df: pd.DataFrame, is_dragon_head: bool = False) -> dict | None:
    """Price deviation from MA5 exceeds threshold (5% normal, 7% dragon_head)."""
    if len(df) < 10:
        return None
    closes = df["close"]
    ma5 = _ma(closes, 5)
    price = float(closes.iloc[-1])
    ma5_val = float(ma5[-1])
    deviation = (price - ma5_val) / ma5_val * 100 if ma5_val > 0 else 0
    limit = 7.0 if is_dragon_head else 5.0

    if abs(deviation) >= limit:
        direction = "above" if deviation > 0 else "below"
        return {
            "rule": "deviation_alert",
            "action": "alert",
            "confidence": 85,
            "price": round(price, 2),
            "ma5": round(ma5_val, 2),
            "deviation_pct": round(deviation, 2),
            "direction": direction,
            "limit": limit,
        }
    return None


def check_ma_alignment(df: pd.DataFrame) -> dict | None:
    """Detect transition into/out of MA5>MA10>MA20 bullish alignment."""
    if len(df) < 25:
        return None
    closes = df["close"]
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)

    # Current alignment
    curr_bullish = ma5[-1] > ma10[-1] > ma20[-1]
    curr_bearish = ma5[-1] < ma10[-1] < ma20[-1]
    # Previous alignment (5 days ago)
    prev_bullish = ma5[-6] > ma10[-6] > ma20[-6]
    prev_bearish = ma5[-6] < ma10[-6] < ma20[-6]

    if curr_bullish and not prev_bullish:
        return {
            "rule": "alignment_turn_bullish",
            "action": "buy",
            "confidence": 70,
            "price": round(float(closes.iloc[-1]), 2),
            "ma5": round(float(ma5[-1]), 2),
            "ma10": round(float(ma10[-1]), 2),
            "ma20": round(float(ma20[-1]), 2),
            "suggested_stop": round(float(closes.iloc[-1]) * 0.96, 2),
        }
    if curr_bearish and not prev_bearish:
        return {
            "rule": "alignment_turn_bearish",
            "action": "sell",
            "confidence": 70,
            "price": round(float(closes.iloc[-1]), 2),
            "ma5": round(float(ma5[-1]), 2),
            "ma10": round(float(ma10[-1]), 2),
            "ma20": round(float(ma20[-1]), 2),
        }
    return None


def check_gap_alert(df: pd.DataFrame) -> dict | None:
    """Detect overnight gap: today's open vs yesterday's close > 3%."""
    if len(df) < 3:
        return None
    today_open = float(df.iloc[-1]["open"])
    prev_close = float(df.iloc[-2]["close"])
    gap_pct = (today_open - prev_close) / prev_close * 100 if prev_close > 0 else 0

    if abs(gap_pct) >= 3:
        return {
            "rule": "gap_up" if gap_pct > 0 else "gap_down",
            "action": "alert",
            "confidence": 80,
            "gap_pct": round(gap_pct, 2),
            "open": round(today_open, 2),
            "prev_close": round(prev_close, 2),
        }
    return None


def check_volume_spike(df: pd.DataFrame) -> dict | None:
    """Volume surge: latest volume > 3x 5d average."""
    if len(df) < 10:
        return None
    volumes = df["volume"]
    avg_vol_5 = volumes.iloc[-6:-1].mean()
    latest_vol = volumes.iloc[-1]
    ratio = latest_vol / avg_vol_5 if avg_vol_5 > 0 else 0

    if ratio >= 3:
        return {
            "rule": "volume_spike",
            "action": "alert",
            "confidence": 65,
            "vol_ratio": round(ratio, 2),
            "latest_volume": int(latest_vol),
            "avg_volume_5d": int(avg_vol_5),
        }
    return None


# Registry of all rule functions
RULES = {
    "ma_cross": check_ma_cross,
    "volume_breakout": check_volume_breakout,
    "deviation": check_deviation_alert,
    "ma_alignment": check_ma_alignment,
    "gap": check_gap_alert,
    "volume_spike": check_volume_spike,
}


def scan_code(code: str, df: pd.DataFrame = None, quote: dict = None,
              rules: list[str] = None, is_dragon_head: bool = False) -> dict:
    """Run all (or specified) rule checks on one stock. Returns {code, signals[], source, time}."""
    if df is None:
        df, source = get_hist(code, days=120)
    else:
        source = "provided"

    if df.empty or len(df) < 10:
        return {"code": code, "signals": [], "source": source,
                "error": "insufficient data"}

    if quote is None:
        try:
            quote, _ = get_quote(code)
        except Exception:
            quote = {}

    names = rules or list(RULES.keys())
    signals = []
    for name in names:
        fn = RULES.get(name)
        if fn is None:
            continue
        try:
            if name == "deviation":
                result = fn(df, is_dragon_head)
            elif name == "volume_breakout":
                result = fn(df, quote)
            else:
                result = fn(df)
            if result:
                result["code"] = code
                result["type"] = "rule"
                signals.append(result)
        except Exception:
            pass

    return {
        "code": code,
        "signals": signals,
        "source": source,
        "time": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def scan_codes(codes: list[str], rules: list[str] = None) -> dict[str, dict]:
    """Batch scan multiple codes. Returns {code: scan_result}."""
    results = {}
    for code in codes:
        results[code] = scan_code(code, rules=rules)
    return results


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "-h" in sys.argv or "--help" in sys.argv:
        print("Rule-based signal scanner — pure Python, zero LLM")
        print("Usage: alphaclaude tools signal_rules CODE [--rule RULE] [--watchlist CODES] [--dragon-head] [--list]")
        print("  CODE            Stock code (6 digits)")
        print("  --rule, -r      Specific rule: " + ", ".join(RULES.keys()))
        print("  --watchlist, -w Comma-separated codes for batch scan")
        print("  --dragon-head   Apply dragon-head relaxation (7% deviation)")
        print("  --list, -l      List available rules")
        sys.exit(0)
    parser = argparse.ArgumentParser(
        description="Rule-based signal scanner — pure Python, zero LLM",
        add_help=False)
    parser.add_argument("code", nargs="?", help="Stock code (6 digits)")
    parser.add_argument("--rule", "-r", choices=list(RULES.keys()),
                        help="Specific rule to run (default: all)")
    parser.add_argument("--watchlist", "-w", default="",
                        help="Comma-separated codes for batch scan")
    parser.add_argument("--dragon-head", action="store_true",
                        help="Apply dragon-head relaxation (7% deviation)")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available rules")
    args = parser.parse_args()

    if args.list:
        rules_info = {
            "ma_cross": "MA5/MA10 golden/death cross (last 3 days)",
            "volume_breakout": "Volume > 1.5x 20d avg + price up > 2%",
            "deviation": "Price deviation from MA5 > 5% (7% for dragon head)",
            "ma_alignment": "MA5>MA10>MA20 alignment transition",
            "gap": "Overnight gap > 3% from prev close",
            "volume_spike": "Volume surge > 3x 5d average",
        }
        print(json.dumps({"rules": rules_info}, ensure_ascii=False, indent=2))
        return

    rules = [args.rule] if args.rule else None

    if args.watchlist:
        codes = [c.strip() for c in args.watchlist.split(",") if c.strip()]
        results = scan_codes(codes, rules=rules)
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return

    if not args.code:
        parser.print_help()
        sys.exit(1)

    result = scan_code(args.code, rules=rules, is_dragon_head=args.dragon_head)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
