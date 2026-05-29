"""Fibonacci retracement and extension level calculator."""
import argparse
import json
import sys

from alphaclaude.tools._fallback import get_hist


RETRACEMENT_RATIOS = [0.236, 0.382, 0.5, 0.618, 0.786]
EXTENSION_RATIOS = [1.0, 1.272, 1.382, 1.618, 2.0, 2.618]


def calc_retracement(high: float, low: float, trend: str = "up") -> dict:
    """Calculate Fibonacci retracement levels.
    trend='up': high to low retracement (回调)
    trend='down': low to high retracement (反弹)
    """
    diff = high - low
    levels = {}
    for ratio in RETRACEMENT_RATIOS:
        if trend == "up":
            level = high - diff * ratio
            label = f"{ratio*100:.1f}%"
        else:
            level = low + diff * ratio
            label = f"{ratio*100:.1f}%"
        levels[label] = round(level, 2)
    return levels


def calc_extension(high: float, low: float, trend: str = "up") -> dict:
    """Calculate Fibonacci extension levels.
    trend='up': upward extension beyond high
    trend='down': downward extension below low
    """
    diff = high - low
    levels = {}
    for ratio in EXTENSION_RATIOS:
        if trend == "up":
            level = high + diff * (ratio - 1.0)
        else:
            level = low - diff * (ratio - 1.0)
        label = f"{ratio*100:.1f}%"
        levels[label] = round(level, 2)
    return levels


def find_swing_points(df, lookback: int = 60) -> dict:
    """Find key swing high/low points for wave analysis."""
    if len(df) < lookback:
        lookback = len(df)
    recent = df.tail(lookback)
    highs = recent["high"].tolist()
    lows = recent["low"].tolist()
    closes = recent["close"].tolist()

    # Find significant swing high (highest point with context)
    swing_high = max(highs)
    swing_low = min(lows)
    current = closes[-1]

    # Determine primary trend direction
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else sum(closes) / len(closes)
    trend = "up" if current > ma20 else "down"

    return {
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "current_price": round(current, 2),
        "primary_trend": trend,
        "range_pct": round((swing_high - swing_low) / swing_low * 100, 2),
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Fibonacci retracement and extension calculator")
    parser.add_argument("code", help="Stock code (6 digits)")
    parser.add_argument("--high", "-hi", type=float, help="Manual swing high price")
    parser.add_argument("--low", "-lo", type=float, help="Manual swing low price")
    parser.add_argument("--trend", "-t", choices=["up", "down"], default="up",
                        help="Trend direction for retracement (default: up)")
    parser.add_argument("--days", "-d", type=int, default=120, help="Historical days for auto-detection")
    args = parser.parse_args()

    code = args.code.zfill(6)

    if args.high and args.low:
        swing_high = args.high
        swing_low = args.low
        source = "manual"
        current = None
        primary_trend = args.trend
    else:
        df, source = get_hist(code, days=args.days)
        if df.empty:
            print(json.dumps({"error": f"No historical data for {code}", "source": source},
                             ensure_ascii=False))
            sys.exit(1)
        sw = find_swing_points(df, min(args.days, len(df)))
        swing_high = sw["swing_high"]
        swing_low = sw["swing_low"]
        current = sw["current_price"]
        primary_trend = sw["primary_trend"]

    retracement = calc_retracement(swing_high, swing_low, primary_trend)
    extension = calc_extension(swing_high, swing_low, primary_trend)

    # Key levels with labels
    result = {
        "code": code,
        "source": source,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "current_price": current,
        "primary_trend": primary_trend,
        "retracement_levels": retracement,
        "extension_levels": extension,
        "key_support": {
            "shallow": retracement.get("38.2%"),
            "moderate": retracement.get("50.0%"),
            "deep": retracement.get("61.8%"),
            "extreme": retracement.get("78.6%"),
        },
        "key_targets": {
            "min_target": extension.get("100.0%"),
            "primary_target": extension.get("161.8%"),
            "aggressive_target": extension.get("261.8%"),
        },
        "wave_rules": {
            "wave2_retrace": f"38.2%~61.8% 即 {retracement.get('38.2%')}~{retracement.get('61.8%')}",
            "wave3_target": f"161.8%~261.8% 即 {extension.get('161.8%')}~{extension.get('261.8%')}",
            "wave4_no_enter_wave1": f"不跌破 {swing_high} (第1浪高点)",
        }
    }

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
