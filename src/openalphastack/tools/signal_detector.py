"""Signal detector: K-line patterns, volume signals, MA cross confirmations."""
import argparse
import json
import sys

from openalphastack.tools._fallback import get_hist


def detect_one_yang_three_yin(df) -> dict:
    """Detect 一阳三阴 pattern in last 5 trading days.
    Day 1: big yang (entity > 2%)
    Day 2-4: three small yin/small candles, volume shrinking, not breaking Day 1 open
    Day 5: yang breaking Day 1 close
    """
    if len(df) < 10:
        return {"signal": "insufficient_data", "description": "需要至少 10 日数据"}

    recent = df.tail(5).reset_index(drop=True)

    # Day 1 (index 0 in recent): big yang
    d1_open = float(recent.iloc[0]["open"])
    d1_close = float(recent.iloc[0]["close"])
    d1_entity_pct = abs(d1_close - d1_open) / d1_open * 100

    if d1_entity_pct < 2 or d1_close <= d1_open:
        return {"signal": "no_match", "reason": f"第1日实体不足({d1_entity_pct:.1f}%)或非阳线"}

    # Day 2-4: three small candles
    shrinking_volume = True
    prev_vol = float(recent.iloc[0]["volume"])
    all_small = True
    all_in_range = True
    day_details = []

    for i in range(1, 4):
        row = recent.iloc[i]
        o, c, _h, lo, v = float(row["open"]), float(row["close"]), float(row["high"]), float(row["low"]), float(row["volume"])
        entity = abs(c - o) / o * 100

        day_info = {"day": i + 1, "entity_pct": round(entity, 2),
                    "is_yang": c >= o, "volume_ratio": round(v / prev_vol, 2) if prev_vol else 0}
        day_details.append(day_info)

        if entity >= 1.5:
            all_small = False
        if lo < d1_open:
            all_in_range = False
        if v > prev_vol:
            shrinking_volume = False
        prev_vol = v

    if not all_small:
        return {"signal": "no_match", "reason": "第2-4日存在非小K线(实体>=1.5%)", "days": day_details}
    if not all_in_range:
        return {"signal": "no_match", "reason": "第2-4日有K线跌破第1日开盘价", "days": day_details}

    # Day 5: breakthrough yang
    d5_open = float(recent.iloc[4]["open"])
    d5_close = float(recent.iloc[4]["close"])

    if d5_close <= d1_close or d5_close <= d5_open:
        return {"signal": "no_match", "reason": f"第5日未突破第1日收盘价({d1_close:.2f})或非阳线",
                "d5_close": round(d5_close, 2), "d1_close": round(d1_close, 2)}

    return {
        "signal": "one_yang_three_yin",
        "buy_price": round(d5_close, 2),
        "stop_loss": round(d1_open * 0.98, 2),
        "details": {
            "d1_entity_pct": round(d1_entity_pct, 2),
            "d1_close": round(d1_close, 2),
            "d5_close": round(d5_close, 2),
            "volume_shrinking": shrinking_volume,
            "days": day_details,
        }
    }


def detect_bottom_volume(df) -> dict:
    """Detect 底部放量: >15% decline from 20d high, volume spike 3x 5d avg, yang candle."""
    if len(df) < 30:
        return {"signal": "insufficient_data", "description": "需要至少 30 日数据"}

    closes = df["close"].tolist()
    volumes = df["volume"].tolist()

    # 20-day high to recent low decline
    high_20d = max(closes[-20:])
    low_recent = min(closes[-5:])
    decline_pct = (high_20d - low_recent) / high_20d * 100

    if decline_pct < 15:
        return {"signal": "no_match", "reason": f"20日高点跌幅不足({decline_pct:.1f}% < 15%)"}

    # Volume spike: latest volume > 3x 5-day average
    avg_vol_5d = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    latest_vol = volumes[-1]
    vol_ratio = latest_vol / avg_vol_5d if avg_vol_5d else 0

    if vol_ratio < 3.0:
        return {"signal": "no_match", "reason": f"量比不足({vol_ratio:.1f}x < 3.0x 5日均量)"}

    # Yang candle
    latest_open = float(df.iloc[-1]["open"])
    latest_close = closes[-1]
    is_yang = latest_close >= latest_open

    if not is_yang:
        return {"signal": "no_match", "reason": "当日非阳线"}

    # Check prior volume was shrinking
    prior_vol_avg = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else avg_vol_5d
    prior_shrinking = avg_vol_5d < prior_vol_avg * 0.8

    # Long lower shadow check
    latest_low = float(df.iloc[-1]["low"])
    shadow_len = latest_open - latest_low if is_yang else latest_close - latest_low
    entity_len = abs(latest_close - latest_open)
    long_shadow = shadow_len > entity_len

    return {
        "signal": "bottom_volume" if is_yang else "no_match",
        "details": {
            "high_20d": round(high_20d, 2),
            "decline_pct": round(decline_pct, 1),
            "latest_volume": int(latest_vol),
            "avg_vol_5d": int(avg_vol_5d),
            "volume_ratio": round(vol_ratio, 2),
            "is_yang": is_yang,
            "prior_vol_shrinking": prior_shrinking,
            "has_long_lower_shadow": long_shadow,
        },
        "stop_loss": round(low_recent * 0.97, 2),
        "max_position_pct": 30,
    }


def detect_shrink_pullback(df) -> dict:
    """Detect 缩量回踩: bullish alignment + price near MA5/MA10 + volume < 70% avg."""
    if len(df) < 30:
        return {"signal": "insufficient_data", "description": "需要至少 30 日数据"}

    closes = df["close"].tolist()
    volumes = df["volume"].tolist()

    # Compute MAs
    def ma(data, period):
        if len(data) < period:
            return None
        return sum(data[-period:]) / period

    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)
    price = closes[-1]

    if not all([ma5, ma10, ma20]):
        return {"signal": "insufficient_data", "description": "均线数据不足"}

    # Bullish prerequisite
    if not (ma5 > ma10 > ma20):
        return {"signal": "no_match", "reason": "非多头排列(MA5>MA10>MA20不满足)"}

    # Pullback detection
    dist_ma5 = abs(price - ma5) / ma5 * 100
    dist_ma10 = abs(price - ma10) / ma10 * 100

    near_ma5 = dist_ma5 <= 1.0
    near_ma10 = dist_ma10 <= 2.0

    if not (near_ma5 or near_ma10):
        return {"signal": "no_match",
                "reason": f"价格未回踩均线(距MA5:{dist_ma5:.1f}%, 距MA10:{dist_ma10:.1f}%)"}

    # Volume shrinkage
    avg_vol_5d = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    latest_vol = volumes[-1]
    vol_pct = latest_vol / avg_vol_5d * 100 if avg_vol_5d else 100

    level = "ma5" if near_ma5 else "ma10"

    if vol_pct < 70:
        return {
            "signal": "shrink_pullback",
            "level": level,
            "buy_price": round(ma5, 2) if level == "ma5" else round(ma10, 2),
            "stop_loss": round(ma20 * 0.98, 2),
            "details": {
                "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
                "dist_ma5_pct": round(dist_ma5, 1),
                "dist_ma10_pct": round(dist_ma10, 1),
                "vol_pct_of_5d_avg": round(vol_pct, 1),
            }
        }
    else:
        return {"signal": "no_match", "reason": f"量能未萎缩({vol_pct:.0f}% >= 70% 5日均量)"}


def detect_volume_breakout(df) -> dict:
    """Detect 放量突破: close above resistance + volume > 2x 5d avg + strong close."""
    if len(df) < 30:
        return {"signal": "insufficient_data", "description": "需要至少 30 日数据"}

    closes = df["close"].tolist()
    highs = df["high"].tolist()
    volumes = df["volume"].tolist()
    price = closes[-1]

    # Resistance: 20-day high (excluding today)
    resistance = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
    if resistance <= 0:
        return {"signal": "no_match", "reason": "无法确定阻力位"}

    # Must close above resistance
    if price <= resistance:
        return {"signal": "no_match", "reason": f"未突破阻力位(现价{price:.2f} <= 阻力{resistance:.2f})"}

    # Volume: > 2x 5-day average
    avg_vol_5d = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    vol_ratio = volumes[-1] / avg_vol_5d if avg_vol_5d else 0

    if vol_ratio < 2.0:
        return {"signal": "no_match", "reason": f"量比不足({vol_ratio:.1f}x < 2.0x 5日均量)"}

    # Strong close: close in upper 30% of day range
    day_high = float(df.iloc[-1]["high"])
    day_low = float(df.iloc[-1]["low"])
    day_range = day_high - day_low
    close_position = (price - day_low) / day_range * 100 if day_range > 0 else 50
    strong_close = close_position >= 70

    if not strong_close:
        return {"signal": "no_match", "reason": f"收盘不强势(收盘在日振幅{close_position:.0f}%位置 < 70%)"}

    return {
        "signal": "volume_breakout",
        "resistance": round(resistance, 2),
        "buy_price": round(price, 2),
        "stop_loss": round(resistance * 0.97, 2),
        "details": {
            "resistance": round(resistance, 2),
            "close_above_pct": round((price - resistance) / resistance * 100, 2),
            "volume_ratio": round(vol_ratio, 2),
            "close_position_pct": round(close_position, 1),
        }
    }


def detect_golden_cross(df) -> dict:
    """Detect 均线金叉 with volume confirmation."""
    if len(df) < 30:
        return {"signal": "insufficient_data", "description": "需要至少 30 日数据"}

    closes = df["close"].tolist()
    volumes = df["volume"].tolist()
    price = closes[-1]

    def ma(data, period):
        if len(data) < period:
            return None
        return [sum(data[i-period+1:i+1]) / period if i >= period - 1 else float('nan')
                for i in range(len(data))]

    ma5_all = ma(closes, 5)
    ma10_all = ma(closes, 10)

    if not ma5_all or not ma10_all:
        return {"signal": "insufficient_data", "description": "均线数据不足"}

    # Check MA5 crossing MA10 in last 3 days
    cross_detected = None
    for i in range(min(3, len(closes) - 2)):
        idx = -(i + 1)
        prev = idx - 1
        if ma5_all[idx] > ma10_all[idx] and ma5_all[prev] <= ma10_all[prev]:
            cross_detected = i + 1
            break

    # Volume confirmation
    avg_vol_5d = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else volumes[-1]
    latest_vol = volumes[-1]
    vol_ok = latest_vol > avg_vol_5d

    # Price deviation
    ma5_val = ma5_all[-1]
    deviation = (price - ma5_val) / ma5_val * 100 if ma5_val and ma5_val != 0 else 0

    trend_background = "unknown"
    if len(df) >= 60:
        # Check prior trend: were MAs converging before crossing?
        spread_10d_ago = abs(ma5_all[-11] - ma10_all[-11]) if len(ma5_all) >= 11 else 0
        spread_now = abs(ma5_all[-1] - ma10_all[-1])
        if spread_10d_ago > spread_now * 2:
            trend_background = "盘整后金叉" if cross_detected else "盘整中"
        elif ma5_all[-1] > ma5_all[-6]:
            trend_background = "上升趋势中"
        else:
            trend_background = "下跌中"

    if cross_detected and vol_ok and deviation < 5:
        return {
            "signal": "ma_golden_cross",
            "cross_days_ago": cross_detected,
            "trend_background": trend_background,
            "buy_price": round(ma5_val, 2),
            "stop_loss": round(price * 0.95, 2),
            "details": {
                "ma5": round(ma5_val, 2), "ma10": round(ma10_all[-1], 2),
                "deviation_pct": round(deviation, 1),
                "volume_ok": vol_ok,
            }
        }
    elif cross_detected:
        return {"signal": "no_match", "reason": f"金叉但条件不满足(量:{'OK' if vol_ok else '不足'} 乖离:{deviation:.1f}%)"}
    else:
        return {"signal": "no_match", "reason": "近3日无MA5上穿MA10"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Signal detector for K-line and volume patterns")
    parser.add_argument("code", help="Stock code (6 digits)")
    parser.add_argument("--signal", "-s", required=True,
                        choices=["one_yang_three_yin", "bottom_volume", "shrink_pullback",
                                 "volume_breakout", "golden_cross", "all"],
                        help="Signal type to detect")
    parser.add_argument("--days", "-d", type=int, default=60, help="Historical days (default: 60)")
    args = parser.parse_args()

    code = args.code.zfill(6)
    df, source = get_hist(code, days=args.days)

    if df.empty:
        print(json.dumps({"error": f"No historical data for {code}", "source": source},
                         ensure_ascii=False))
        sys.exit(1)

    if args.signal == "all":
        result = {
            "code": code, "source": source,
            "one_yang_three_yin": detect_one_yang_three_yin(df),
            "bottom_volume": detect_bottom_volume(df),
            "shrink_pullback": detect_shrink_pullback(df),
            "volume_breakout": detect_volume_breakout(df),
            "golden_cross": detect_golden_cross(df),
        }
    elif args.signal == "one_yang_three_yin":
        result = detect_one_yang_three_yin(df)
    elif args.signal == "bottom_volume":
        result = detect_bottom_volume(df)
    elif args.signal == "shrink_pullback":
        result = detect_shrink_pullback(df)
    elif args.signal == "volume_breakout":
        result = detect_volume_breakout(df)
    elif args.signal == "golden_cross":
        result = detect_golden_cross(df)

    result["code"] = code
    result["source"] = source
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
