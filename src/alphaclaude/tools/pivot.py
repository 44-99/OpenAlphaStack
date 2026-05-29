"""Pivot point detection: swing highs/lows, support/resistance clusters, box range."""
import argparse
import json
import sys

from alphaclaude.tools._fallback import get_hist


def find_pivots(highs: list, lows: list, window: int = 5) -> dict:
    """Find pivot highs and lows using a rolling window.
    A pivot high is a point that's higher than `window` bars on each side.
    """
    pivot_highs = []
    pivot_lows = []

    for i in range(window, len(highs) - window):
        h = highs[i]
        lo = lows[i]
        # Pivot high: higher than all bars in window on each side
        if h == max(highs[i - window:i + window + 1]):
            pivot_highs.append({"index": i, "price": round(h, 2)})
        # Pivot low: lower than all bars in window on each side
        if lo == min(lows[i - window:i + window + 1]):
            pivot_lows.append({"index": i, "price": round(lo, 2)})

    return {"pivot_highs": pivot_highs, "pivot_lows": pivot_lows, "window": window}


def cluster_levels(pivots: list, tolerance_pct: float = 2.0) -> list:
    """Cluster nearby pivot points into support/resistance levels.
    Returns levels sorted by price, each with count of touches.
    """
    if not pivots:
        return []

    sorted_pivots = sorted(pivots, key=lambda x: x["price"])
    clusters = []
    current = {"price": sorted_pivots[0]["price"], "touches": 1, "prices": [sorted_pivots[0]["price"]]}

    for p in sorted_pivots[1:]:
        if abs(p["price"] - current["price"]) / current["price"] * 100 <= tolerance_pct:
            current["touches"] += 1
            current["prices"].append(p["price"])
            current["price"] = round(sum(current["prices"]) / len(current["prices"]), 2)
        else:
            clusters.append(current)
            current = {"price": p["price"], "touches": 1, "prices": [p["price"]]}
    clusters.append(current)

    return sorted(clusters, key=lambda x: x["touches"], reverse=True)


def find_box_range(df, min_touches: int = 2) -> dict:
    """Identify box range (箱体) from pivot clusters: support/resistance zones."""
    if len(df) < 60:
        return {"signal": "insufficient_data", "description": "需要至少 60 日数据识别箱体"}

    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    price = closes[-1]

    pivots = find_pivots(highs, lows, window=5)
    resistance_clusters = cluster_levels(pivots["pivot_highs"], tolerance_pct=3.0)
    support_clusters = cluster_levels(pivots["pivot_lows"], tolerance_pct=3.0)

    # Valid clusters: >= min_touches
    valid_resistance = [c for c in resistance_clusters if c["touches"] >= min_touches]
    valid_support = [c for c in support_clusters if c["touches"] >= min_touches]

    if not valid_resistance or not valid_support:
        return {"signal": "no_box",
                "description": "未找到足够的枢轴点聚类(每个边界>=2次触碰)",
                "resistance_clusters": len(valid_resistance),
                "support_clusters": len(valid_support)}

    # Best box: highest-touch resistance and support
    top = valid_resistance[0]["price"] if valid_resistance else max(highs[-60:])
    bottom = valid_support[0]["price"] if valid_support else min(lows[-60:])

    # Ensure top > bottom
    if top <= bottom:
        return {"signal": "no_box", "description": "阻力位低于支撑位，无有效箱体"}

    box_width = (top - bottom) / bottom * 100
    position_in_box = (price - bottom) / (top - bottom) * 100 if top != bottom else 50

    # Determine zone
    if position_in_box <= 33:
        zone = "箱底区域"
        action = "买入/加仓"
    elif position_in_box >= 67:
        zone = "箱顶区域"
        action = "减仓/止盈"
    else:
        zone = "箱中区域"
        action = "观望"

    return {
        "signal": "box_identified",
        "box_top": round(top, 2),
        "box_bottom": round(bottom, 2),
        "box_width_pct": round(box_width, 1),
        "current_price": round(price, 2),
        "position_in_box_pct": round(position_in_box, 1),
        "zone": zone,
        "action": action,
        "resistance_touches": valid_resistance[0]["touches"],
        "support_touches": valid_support[0]["touches"],
        "stop_loss": round(bottom * 0.97, 2),
        "target": round(top, 2),
        "validity": "valid" if box_width >= 5 else "narrow",
    }


def find_zhongshu(df) -> dict:
    """Identify 缠论中枢: overlapping zones of 3+ segments.
    Simplified: finds overlapping pivot zones from recent data.
    """
    if len(df) < 60:
        return {"signal": "insufficient_data", "description": "需要至少 60 日数据"}

    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()
    price = closes[-1]

    pivots = find_pivots(highs, lows, window=4)
    ph = pivots["pivot_highs"]
    pl = pivots["pivot_lows"]

    # Find overlapping zones from recent pivots (last 3 of each)
    recent_highs = sorted(ph[-6:], key=lambda x: x["price"]) if len(ph) >= 3 else ph
    recent_lows = sorted(pl[-6:], key=lambda x: x["price"], reverse=True) if len(pl) >= 3 else pl

    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        # Zhongshu top: lower of the top two pivot highs
        zhongshu_top = min(recent_highs[-2:], key=lambda x: x["price"])["price"]
        # Zhongshu bottom: higher of the bottom two pivot lows
        zhongshu_bottom = max(recent_lows[:2], key=lambda x: x["price"])["price"]

        if zhongshu_top > zhongshu_bottom:
            width = (zhongshu_top - zhongshu_bottom) / zhongshu_bottom * 100
            above = price > zhongshu_top
            below = price < zhongshu_bottom

            if above:
                direction = "向上离开中枢"
            elif below:
                direction = "向下离开中枢"
            else:
                direction = "中枢内震荡"

            return {
                "signal": "zhongshu_identified",
                "zhongshu_top": round(zhongshu_top, 2),
                "zhongshu_bottom": round(zhongshu_bottom, 2),
                "width_pct": round(width, 1),
                "current_price": round(price, 2),
                "direction": direction,
                "buy_point_type": _classify_buy_point(price, zhongshu_top, zhongshu_bottom, above, below),
            }

    return {"signal": "no_zhongshu", "description": "当前数据不足以识别有效中枢",
            "pivot_count": {"highs": len(ph), "lows": len(pl)}}


def _classify_buy_point(price, zh_top, zh_bottom, above, below) -> str:
    """Classify 缠论 buy point type."""
    if above:
        return "三买（离开中枢后不回中枢）"
    elif below:
        # Check if price made new low while MACD diverged (need external MACD data)
        return "可能一买（需MACD底背驰确认）"
    else:
        return "中枢震荡（无明确买卖点，可二买/二卖位置等待）"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Pivot point detection: support/resistance, box range, 缠论中枢")
    parser.add_argument("code", help="Stock code (6 digits)")
    parser.add_argument("--mode", "-m", choices=["box", "zhongshu", "all"], default="all",
                        help="Analysis mode")
    parser.add_argument("--window", "-w", type=int, default=5, help="Pivot detection window (default: 5)")
    parser.add_argument("--days", "-d", type=int, default=120, help="Historical days (default: 120)")
    args = parser.parse_args()

    code = args.code.zfill(6)
    df, source = get_hist(code, days=args.days)

    if df.empty:
        print(json.dumps({"error": f"No historical data for {code}", "source": source},
                         ensure_ascii=False))
        sys.exit(1)

    result = {"code": code, "source": source}

    if args.mode in ("box", "all"):
        result["box_range"] = find_box_range(df)

    if args.mode in ("zhongshu", "all"):
        result["zhongshu"] = find_zhongshu(df)

    # Always include basic pivot data
    if args.mode == "all":
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        pivots = find_pivots(highs, lows, window=args.window)
        result["pivot_summary"] = {
            "pivot_high_count": len(pivots["pivot_highs"]),
            "pivot_low_count": len(pivots["pivot_lows"]),
            "recent_pivot_highs": [{"price": p["price"]} for p in pivots["pivot_highs"][-3:]],
            "recent_pivot_lows": [{"price": p["price"]} for p in pivots["pivot_lows"][-3:]],
        }

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
