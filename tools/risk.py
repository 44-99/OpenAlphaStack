"""Deterministic risk calculator — volatility, position sizing, correlation.
Ported from ai-hedge-fund src/agents/risk_manager.py (pure math, no LLM).
"""
import argparse
import json
import math
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fallback import get_hist


def calc_volatility_metrics(closes: list[float], lookback: int = 60) -> dict:
    """Daily volatility → annualized volatility → volatility percentile."""
    if len(closes) < 2:
        return {"daily_volatility": 0.025, "annualized_volatility": 0.25,
                "volatility_percentile": 100, "data_points": len(closes)}

    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    if len(returns) < 2:
        return {"daily_volatility": 0.025, "annualized_volatility": 0.25,
                "volatility_percentile": 100, "data_points": len(returns)}

    recent = returns[-min(lookback, len(returns)):]
    mean_r = sum(recent) / len(recent)
    variance = sum((r - mean_r) ** 2 for r in recent) / len(recent)
    daily_vol = math.sqrt(variance)
    annualized_vol = daily_vol * math.sqrt(252)

    # Percentile: compare current vol vs rolling 30d vol history
    if len(returns) >= 30:
        rolling_vols = []
        for i in range(30, len(returns)):
            window = returns[i - 30:i]
            w_mean = sum(window) / 30
            w_var = sum((r - w_mean) ** 2 for r in window) / 30
            rolling_vols.append(math.sqrt(w_var))
        if rolling_vols:
            percentile = sum(1 for v in rolling_vols if v <= daily_vol) / len(rolling_vols) * 100
        else:
            percentile = 50
    else:
        percentile = 50

    return {
        "daily_volatility": round(daily_vol, 6),
        "annualized_volatility": round(annualized_vol, 4),
        "volatility_percentile": round(percentile, 1),
        "data_points": len(returns),
    }


def calc_volatility_adjusted_limit(annualized_vol: float) -> float:
    """Map volatility to position limit as % of portfolio.

    Logic from ai-hedge-fund risk_manager.py:270:
      Low vol (<15%)  → up to 25% allocation
      Med vol (15-30%) → 15-20%
      High vol (30-50%) → 10-15%
      Very high (>50%) → max 10%
    """
    base = 0.20
    if annualized_vol < 0.15:
        multiplier = 1.25
    elif annualized_vol < 0.30:
        multiplier = 1.0 - (annualized_vol - 0.15) * 0.5
    elif annualized_vol < 0.50:
        multiplier = 0.75 - (annualized_vol - 0.30) * 0.5
    else:
        multiplier = 0.50
    multiplier = max(0.25, min(1.25, multiplier))
    return round(base * multiplier, 4)


def calc_correlation_multiplier(avg_correlation: float) -> float:
    """Map average correlation to adjustment multiplier.

    Logic from ai-hedge-fund risk_manager.py:301:
      Very high (>=0.80) → 0.70x
      High (0.60-0.80) → 0.85x
      Moderate (0.40-0.60) → 1.00x
      Low (0.20-0.40) → 1.05x
      Very low (<0.20) → 1.10x
    """
    if avg_correlation >= 0.80:
        return 0.70
    if avg_correlation >= 0.60:
        return 0.85
    if avg_correlation >= 0.40:
        return 1.00
    if avg_correlation >= 0.20:
        return 1.05
    return 1.10


def calc_position_size(price: float, capital: float, limit_pct: float,
                       max_drawdown_pct: float = 0.15) -> dict:
    """Calculate suggested shares and position value."""
    position_value = capital * limit_pct
    max_shares = int(position_value // (price * 100)) * 100  # lot size 100
    if max_shares < 100:
        max_shares = 0
    actual_value = max_shares * price
    return {
        "position_limit_pct": round(limit_pct * 100, 1),
        "position_value": round(actual_value, 2),
        "max_shares": max_shares,
        "max_loss_at_stop": round(actual_value * max_drawdown_pct, 2),
    }


def max_drawdown_check(close_prices: list[float]) -> dict:
    """Calculate current drawdown vs historical max drawdown from price series."""
    if len(close_prices) < 2:
        return {"current_drawdown_pct": 0, "max_historical_drawdown_pct": 0, "warn": False}

    # Track drawdown on price series directly
    peak = close_prices[0]
    max_dd = 0.0
    for p in close_prices:
        if p > peak:
            peak = p
        dd = (peak - p) / peak
        if dd > max_dd:
            max_dd = dd

    current_dd = (peak - close_prices[-1]) / peak if peak else 0
    warn = current_dd > 0.15

    return {
        "current_drawdown_pct": round(current_dd * 100, 1),
        "max_historical_drawdown_pct": round(max_dd * 100, 1),
        "warn": warn,
    }


def calc_portfolio_correlation(primary_closes: list[float],
                               other_closes: list[dict]) -> dict:
    """Calculate average correlation between primary stock and other positions."""
    if not other_closes or len(primary_closes) < 5:
        return {"avg_correlation": 0, "correlations": [], "note": "insufficient data"}

    primary_returns = [
        (primary_closes[i] - primary_closes[i - 1]) / primary_closes[i - 1]
        for i in range(1, len(primary_closes))
    ]

    correlations = []
    for pos in other_closes:
        closes = pos.get("closes", [])
        if len(closes) < 5:
            continue
        other_returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
        ]
        min_len = min(len(primary_returns), len(other_returns))
        if min_len < 5:
            continue
        p_r = primary_returns[-min_len:]
        o_r = other_returns[-min_len:]
        p_mean = sum(p_r) / min_len
        o_mean = sum(o_r) / min_len
        num = sum((p_r[i] - p_mean) * (o_r[i] - o_mean) for i in range(min_len))
        den_p = math.sqrt(sum((r - p_mean) ** 2 for r in p_r))
        den_o = math.sqrt(sum((r - o_mean) ** 2 for r in o_r))
        corr = num / (den_p * den_o) if den_p and den_o else 0
        correlations.append({"code": pos["code"], "correlation": round(corr, 4)})

    avg_corr = sum(c["correlation"] for c in correlations) / len(correlations) if correlations else 0
    return {
        "avg_correlation": round(avg_corr, 4),
        "correlations": correlations,
        "correlation_multiplier": calc_correlation_multiplier(avg_corr),
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Risk calculator: volatility, position sizing, correlation")
    parser.add_argument("code", help="Stock code (6 digits)")
    parser.add_argument("--capital", "-c", type=float, default=100000,
                        help="Total capital (default: 100000)")
    parser.add_argument("--positions", "-p", nargs="*", default=[],
                        help="Other position codes (e.g. 000858 002594)")
    args = parser.parse_args()

    code = args.code.zfill(6)
    df, source = get_hist(code, days=120)

    if df.empty:
        print(json.dumps({"error": f"No historical data for {code}"}, ensure_ascii=False))
        sys.exit(1)

    closes = df["close"].tolist()

    # Volatility metrics
    vol = calc_volatility_metrics(closes)

    # Position limit from volatility
    limit_pct = calc_volatility_adjusted_limit(vol["annualized_volatility"])

    # Get current price
    price = float(closes[-1])

    # Drawdown check
    dd = max_drawdown_check(closes)

    # Portfolio correlation (if other positions given)
    corr = {}
    corr_mult = 1.0

    # Placeholder for future other-positions data
    # For now, other positions default to empty — correlation = 0

    # Adjusted limit with correlation
    adjusted_limit = limit_pct * corr_mult

    # Position sizing
    sizing = calc_position_size(price, args.capital, adjusted_limit)

    # Risk level classification
    annualized_vol = vol["annualized_volatility"]
    if annualized_vol < 0.15:
        risk_level = "low"
    elif annualized_vol < 0.30:
        risk_level = "medium"
    elif annualized_vol < 0.50:
        risk_level = "high"
    else:
        risk_level = "extreme"

    result = {
        "code": code,
        "price": round(price, 2),
        "capital": args.capital,
        "source": source,
        "volatility": vol,
        "risk_level": risk_level,
        "position_limit_raw": round(limit_pct * 100, 1),
        "correlation_adjustment": corr,
        "position_limit_adjusted": round(adjusted_limit * 100, 1),
        "sizing": sizing,
        "drawdown": dd,
        "rules": {
            "deviation_check": {
                "rule": "乖离率 ≤ 5% (龙头 ≤ 7%)",
                "hard": True,
            },
            "trend_requirement": {
                "rule": "MA5 > MA10 > MA20 多头排列",
                "hard": True,
            },
            "single_stock_limit": {
                "rule": "单只股票 ≤ 25% 总仓位",
                "hard": True,
            },
        },
    }

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
