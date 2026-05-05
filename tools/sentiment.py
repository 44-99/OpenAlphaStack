"""Market sentiment: turnover trend, volume trend, ATR, MA convergence."""
import argparse
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fallback import get_hist


def calc_atr(df, period: int = 14) -> list:
    """Calculate Average True Range."""
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    closes = df["close"].tolist()

    tr_values = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_values.append(tr)

    atr = []
    if len(tr_values) >= period:
        atr.append(sum(tr_values[:period]) / period)
        for i in range(period, len(tr_values)):
            atr.append((atr[-1] * (period - 1) + tr_values[i]) / period)
    return atr


def turnover_trend(df) -> dict:
    """Analyze turnover rate trend over 20 days."""
    if "turnover" not in df.columns and len(df) < 20:
        return {"error": "换手率数据不足(需要20日)"}

    # Estimate turnover from volume if not available
    if "turnover" not in df.columns:
        volumes = df["volume"].tolist()
        # Normalize: recent 20d vs prior period
        recent_avg = sum(volumes[-20:]) / 20
        prior_avg = sum(volumes[-40:-20]) / min(20, len(volumes) - 20) if len(volumes) >= 40 else recent_avg
        ratio_recent_vs_prior = recent_avg / prior_avg if prior_avg else 1.0
        rising = recent_avg > prior_avg * 1.1
        falling = recent_avg < prior_avg * 0.9
        return {
            "turnover_available": False,
            "volume_as_proxy": True,
            "recent_20d_avg_volume": int(recent_avg),
            "prior_20d_avg_volume": int(prior_avg) if len(volumes) >= 40 else None,
            "trend": "rising" if rising else ("falling" if falling else "stable"),
        }

    turnovers = df["turnover"].tolist()
    recent_avg = sum(turnovers[-20:]) / 20 if len(turnovers) >= 20 else sum(turnovers) / len(turnovers)
    prior_avg = sum(turnovers[-40:-20]) / 20 if len(turnovers) >= 40 else recent_avg
    recent_max = max(turnovers[-20:]) if len(turnovers) >= 20 else max(turnovers)
    recent_min = min(turnovers[-20:]) if len(turnovers) >= 20 else min(turnovers)
    current = turnovers[-1] if turnovers else 0

    rising = recent_avg > prior_avg * 1.1
    falling = recent_avg < prior_avg * 0.9
    trend = "rising" if rising else ("falling" if falling else "stable")

    # Sentiment heat level
    if current > 10:
        heat = "extreme_heat"
    elif current > 5:
        heat = "high_heat"
    elif current > 2:
        heat = "active"
    elif current > 0.5:
        heat = "normal"
    else:
        heat = "cold"

    return {
        "turnover_available": True,
        "current_turnover": round(current, 2),
        "recent_20d_avg": round(recent_avg, 2),
        "prior_20d_avg": round(prior_avg, 2) if len(turnovers) >= 40 else None,
        "recent_20d_high": round(recent_max, 2),
        "recent_20d_low": round(recent_min, 2),
        "trend": trend,
        "heat_level": heat,
    }


def volume_trend(df) -> dict:
    """Analyze volume trend over 60 days."""
    volumes = df["volume"].tolist()
    if len(volumes) < 20:
        return {"error": "成交量数据不足"}

    avg_20d = sum(volumes[-20:]) / 20
    avg_60d = sum(volumes[-60:]) / min(60, len(volumes)) if len(volumes) >= 60 else avg_20d
    latest = volumes[-1]

    # Volume contraction below 50% of 60d avg
    below_50pct = latest < avg_60d * 0.5
    # Volume surge (single day spike > 3x 20d avg)
    surge = latest > avg_20d * 3.0

    # Trend direction
    if len(volumes) >= 40:
        recent_10 = sum(volumes[-10:]) / 10
        prior_10 = sum(volumes[-20:-10]) / 10
        vol_trend = "rising" if recent_10 > prior_10 * 1.1 else ("falling" if recent_10 < prior_10 * 0.9 else "stable")
    else:
        vol_trend = "insufficient_data"

    return {
        "latest_volume": int(latest),
        "avg_20d_volume": int(avg_20d),
        "avg_60d_volume": int(avg_60d) if len(volumes) >= 60 else None,
        "vs_60d_pct": round(latest / avg_60d * 100, 1) if avg_60d else None,
        "below_50pct_60d": below_50pct,
        "volume_surge": surge,
        "trend": vol_trend,
    }


def ma_convergence(df) -> dict:
    """Check if MAs are converging (均线粘合)."""
    closes = df["close"].tolist()
    if len(closes) < 20:
        return {"error": "数据不足"}

    def ma(data, p):
        return sum(data[-p:]) / p if len(data) >= p else None

    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)

    if not all([ma5, ma10, ma20]):
        return {"error": "均线计算失败"}

    # How close are the three MAs as % of price
    spread = max(ma5, ma10, ma20) - min(ma5, ma10, ma20)
    spread_pct = spread / ma20 * 100

    converged = spread_pct < 2.0
    contracting = False

    if len(closes) >= 40:
        ma5_old = ma(closes[:-20], 5)
        ma10_old = ma(closes[:-20], 10)
        ma20_old = ma(closes[:-20], 20)
        if ma5_old and ma10_old and ma20_old:
            old_spread = max(ma5_old, ma10_old, ma20_old) - min(ma5_old, ma10_old, ma20_old)
            old_spread_pct = old_spread / ma20_old * 100
            contracting = spread_pct < old_spread_pct * 0.7

    return {
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "spread_pct": round(spread_pct, 2),
        "converged": converged,
        "contracting": contracting,
        "signal": "均线粘合，蓄势待发" if converged else (
            "均线正在收缩" if contracting else "均线发散"
        ),
    }


def sentiment_score(turnover: dict, volume: dict, ma_conv: dict, atr_data: dict | None = None) -> dict:
    """Composite sentiment scoring based on multiple indicators."""
    score = 0
    factors = []

    # Turnover heat
    if "heat_level" in turnover:
        heat = turnover["heat_level"]
        if heat == "cold":
            score += 14
            factors.append({"factor": "换手率极低(冷淡)", "score": +14})
        elif heat == "normal":
            factors.append({"factor": "换手率正常(平稳)", "score": 0})
        elif heat == "active":
            score += 5
            factors.append({"factor": "换手率活跃", "score": +5})
        elif heat == "high_heat":
            score -= 8
            factors.append({"factor": "换手率过热", "score": -8})
        elif heat == "extreme_heat":
            score -= 15
            factors.append({"factor": "换手率极度过热", "score": -15})

    # Volume conditions
    if volume.get("below_50pct_60d"):
        score += 8
        factors.append({"factor": "量能极度萎缩(底部特征)", "score": +8})
    if volume.get("volume_surge"):
        score -= 5
        factors.append({"factor": "单日暴量(警惕出货)", "score": -5})

    # MA convergence
    if ma_conv.get("converged"):
        score += 10
        factors.append({"factor": "均线粘合(蓄势)", "score": +10})
    elif ma_conv.get("contracting"):
        score += 5
        factors.append({"factor": "均线正在收缩", "score": +5})

    # ATR contraction
    if atr_data and atr_data.get("contracting"):
        score += 5
        factors.append({"factor": "ATR萎缩(波动率降低)", "score": +5})

    # Determine overall stage
    if score >= 14:
        stage = "冷淡底部"
        advice = "逆情绪布局：大众恐慌时贪婪"
    elif score >= 5:
        stage = "升温介入"
        advice = "可适度参与"
    elif score >= -5:
        stage = "平稳"
        advice = "正常交易节奏"
    elif score >= -12:
        stage = "过热警惕"
        advice = "减仓信号：警惕情绪顶"
    else:
        stage = "狂热顶部"
        advice = "离场：大众贪婪时谨慎"

    return {
        "total_score": score,
        "stage": stage,
        "advice": advice,
        "factors": factors,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Market sentiment: turnover, volume, ATR, MA convergence")
    parser.add_argument("code", help="Stock code (6 digits, or 'market' for market overview)")
    parser.add_argument("--days", "-d", type=int, default=100, help="Historical days (default: 100)")
    args = parser.parse_args()

    code = args.code.zfill(6)
    df, source = get_hist(code, days=args.days)

    if df.empty:
        print(json.dumps({"error": f"No historical data for {code}", "source": source},
                         ensure_ascii=False))
        sys.exit(1)

    tt = turnover_trend(df)
    vt = volume_trend(df)
    mc = ma_convergence(df)

    # ATR
    atr_vals = calc_atr(df, period=14)
    atr_current = round(atr_vals[-1], 4) if atr_vals else None
    atr_contracting = False
    if len(atr_vals) >= 20:
        recent_atr = sum(atr_vals[-10:]) / 10
        prior_atr = sum(atr_vals[-20:-10]) / 10
        atr_contracting = recent_atr < prior_atr * 0.8

    atr_info = {
        "atr_14": atr_current,
        "contracting": atr_contracting,
    } if atr_current else None

    score = sentiment_score(tt, vt, mc, atr_info)

    result = {
        "code": code,
        "source": source,
        "turnover_trend": tt,
        "volume_trend": vt,
        "ma_convergence": mc,
        "atr": atr_info,
        "sentiment": score,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
