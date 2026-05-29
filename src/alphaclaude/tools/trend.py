"""Trend analysis: MA alignment, crossover detection, deviation, trend status."""
import argparse
import json
import sys

from alphaclaude.tools._fallback import get_hist


def ma_alignment(prices: list, ma5: list, ma10: list, ma20: list) -> dict:
    """Check MA alignment state."""
    ma20_slope = (ma20[-1] - ma20[-6]) / ma20[-6] * 100 if len(ma20) >= 6 and ma20[-6] != 0 else 0

    if ma5[-1] > ma10[-1] > ma20[-1] and ma20_slope > 0:
        return {"alignment": "bullish", "status": "STRONG_BULL",
                "description": "多头排列，MA20向上"}
    elif ma5[-1] > ma10[-1] > ma20[-1]:
        return {"alignment": "bullish", "status": "BULL",
                "description": "多头排列，MA20走平"}
    elif ma5[-1] < ma10[-1] < ma20[-1] and ma20_slope < 0:
        return {"alignment": "bearish", "status": "STRONG_BEAR",
                "description": "空头排列，MA20向下"}
    elif ma5[-1] < ma10[-1] < ma20[-1]:
        return {"alignment": "bearish", "status": "BEAR",
                "description": "空头排列，MA20走平"}
    else:
        return {"alignment": "sideways", "status": "SIDEWAYS",
                "description": "均线缠绕，方向不明"}


def detect_crossover(ma_fast: list, ma_slow: list, days: int = 3) -> dict:
    """Detect if fast MA crossed above/below slow MA within recent days.
    Returns golden_cross / death_cross / none with cross date."""
    if len(ma_fast) < days + 2 or len(ma_slow) < days + 2:
        return {"type": "none", "cross_date": None, "reason": "insufficient data"}

    window = min(days, len(ma_fast) - 1)
    for i in range(window):
        idx = -(i + 1)
        prev = idx - 1
        if ma_fast[idx] > ma_slow[idx] and ma_fast[prev] <= ma_slow[prev]:
            return {"type": "golden_cross", "cross_date": i,
                    "description": f"约 {i + 1} 个交易日前上穿"}
        if ma_fast[idx] < ma_slow[idx] and ma_fast[prev] >= ma_slow[prev]:
            return {"type": "death_cross", "cross_date": i,
                    "description": f"约 {i + 1} 个交易日前下穿"}
    return {"type": "none", "cross_date": None, "description": f"近 {window} 日内无交叉"}


def calc_deviation(price: float, ma_values: dict) -> dict:
    """Calculate price deviation from each MA."""
    result = {}
    for key, ma_val in ma_values.items():
        if ma_val and ma_val != 0:
            pct = round((price - ma_val) / ma_val * 100, 2)
            result[key] = {"value": round(ma_val, 2), "deviation_pct": pct,
                           "zone": "best_buy" if abs(pct) < 2 else
                                   ("small_position" if abs(pct) < 5 else
                                    ("overbought" if pct > 5 else "oversold"))}
    return result


def compute_mas(closes: list) -> dict:
    """Compute MA5/10/20/60 from close prices."""
    ma = {}
    for period in [5, 10, 20, 60]:
        key = f"ma{period}"
        if len(closes) >= period:
            ma[key] = [sum(closes[i-period+1:i+1]) / period if i >= period - 1 else float('nan')
                       for i in range(len(closes))]
        else:
            ma[key] = [float('nan')] * len(closes)
    return ma


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Trend analysis: MA alignment, crossovers, deviation")
    parser.add_argument("code", help="Stock code (6 digits)")
    parser.add_argument("--check", "-c", choices=["alignment", "cross", "deviation", "status", "all"],
                        default="all", help="What to check")
    parser.add_argument("--days", "-d", type=int, default=60, help="Historical days (default: 60)")
    args = parser.parse_args()

    code = args.code.zfill(6)
    df, source = get_hist(code, days=max(args.days, 60))

    if df.empty:
        print(json.dumps({"error": f"No historical data for {code}", "source": source},
                         ensure_ascii=False))
        sys.exit(1)

    closes = df["close"].tolist()
    mas = compute_mas(closes)

    # Clean NaN tails
    ma5 = [v for v in mas["ma5"] if not (isinstance(v, float) and v != v)]
    ma10 = [v for v in mas["ma10"] if not (isinstance(v, float) and v != v)]
    ma20 = [v for v in mas["ma20"] if not (isinstance(v, float) and v != v)]
    ma60 = [v for v in mas["ma60"] if not (isinstance(v, float) and v != v)]
    price = closes[-1]

    result = {"code": code, "source": source, "price": round(price, 2)}

    if args.check in ("alignment", "all"):
        result["alignment"] = ma_alignment(closes, ma5, ma10, ma20)

    if args.check in ("cross", "all"):
        result["crossovers"] = {
            "ma5_ma10": detect_crossover(ma5, ma10, days=3),
            "ma10_ma20": detect_crossover(ma10, ma20, days=5),
        }

    if args.check in ("deviation", "all"):
        result["deviation"] = calc_deviation(price, {"MA5": ma5[-1] if ma5 else None,
                                                      "MA10": ma10[-1] if ma10 else None,
                                                      "MA20": ma20[-1] if ma20 else None,
                                                      "MA60": ma60[-1] if ma60 else None})

    if args.check in ("status", "all"):
        # Combined trend status
        align = ma_alignment(closes, ma5, ma10, ma20)
        ma60_slope = (ma60[-1] - ma60[-11]) / ma60[-11] * 100 if len(ma60) >= 11 and ma60[-11] != 0 else 0
        price_vs_ma20 = (price - ma20[-1]) / ma20[-1] * 100 if ma20 and ma20[-1] != 0 else 0
        result["trend_status"] = {
            "ma_alignment": align["status"],
            "price_vs_ma20_pct": round(price_vs_ma20, 2),
            "ma60_slope_pct": round(ma60_slope, 2),
            "latest_close": round(price, 2),
            "latest_ma5": round(ma5[-1], 2) if ma5 else None,
            "latest_ma10": round(ma10[-1], 2) if ma10 else None,
            "latest_ma20": round(ma20[-1], 2) if ma20 else None,
        }

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
