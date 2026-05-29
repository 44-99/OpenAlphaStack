"""Strategy variant pipeline — market-state-driven parameter switching.

Three presets (conservative / default / aggressive) selected based on
market volatility and trend direction. Avoids confirmation bias from
always using the same parameters regardless of market regime.
"""
import json
from alphaclaude.paths import PROJECT_ROOT
import sys

PROJECT_DIR = str(PROJECT_ROOT)

VARIANTS: dict[str, dict] = {
    "default": {
        "name": "默认",
        "position_cap_by_bias": {"bullish": 80, "neutral": 50, "bearish": 20},
        "source_b_max_pct": 20.0,
        "source_b_stop_pct": -8,
        "source_c_max_pct": 7.5,
        "source_c_stop_pct": -5,
        "max_single_position_pct": 25.0,
        "signal_min_confidence": 65,
        "signal_position_pct": 0.075,
        "max_total_position_pct": 80.0,
    },
    "conservative": {
        "name": "保守",
        "position_cap_by_bias": {"bullish": 55, "neutral": 35, "bearish": 15},
        "source_b_max_pct": 15.0,
        "source_b_stop_pct": -5,
        "source_c_max_pct": 5.0,
        "source_c_stop_pct": -3,
        "max_single_position_pct": 15.0,
        "signal_min_confidence": 75,
        "signal_position_pct": 0.05,
        "max_total_position_pct": 55.0,
    },
    "aggressive": {
        "name": "进取",
        "position_cap_by_bias": {"bullish": 90, "neutral": 60, "bearish": 25},
        "source_b_max_pct": 25.0,
        "source_b_stop_pct": -10,
        "source_c_max_pct": 10.0,
        "source_c_stop_pct": -7,
        "max_single_position_pct": 30.0,
        "signal_min_confidence": 60,
        "signal_position_pct": 0.10,
        "max_total_position_pct": 90.0,
    },
}


def classify_market_state() -> str:
    """Classify current market: 'low_vol_bullish', 'high_vol_bearish', or 'normal'.

    Uses index MA alignment for trend + recent daily range for volatility.
    Returns 'normal' if data is unavailable.
    """
    try:
        from alphaclaude.tools.quote import get_market_overview
        overview = get_market_overview()
        if overview.get("error"):
            return "normal"

        indices = overview.get("indices", {})
        sh = indices.get("上证指数", {})
        if not sh:
            return "normal"

        change_pct = sh.get("change_pct", 0)
        # Rough volatility: if single-day move > 2%, high vol
        abs_change = abs(change_pct)

        # Check trend via MA alignment from technical tool
        trend_bullish = _check_trend_bullish()

        if abs_change > 2.0 and (change_pct < -1.5 or not trend_bullish):
            return "high_vol_bearish"
        if abs_change < 0.8 and trend_bullish:
            return "low_vol_bullish"
        return "normal"
    except Exception:
        return "normal"


def _check_trend_bullish() -> bool:
    """Check if Shanghai index is in bullish MA alignment (MA5 > MA10 > MA20)."""
    try:
        from alphaclaude.tools.technical import get_technical
        data = get_technical("000001", indicators=["ma"])
        if data.get("error"):
            return False
        ma = data.get("ma", {})
        ma5 = ma.get("ma5", 0)
        ma10 = ma.get("ma10", 0)
        ma20 = ma.get("ma20", 0)
        if ma5 and ma10 and ma20:
            return ma5 > ma10 > ma20
        return False
    except Exception:
        return False


def select_variant(state: str = "", force: str = "") -> dict:
    """Select the active variant based on market state.

    Args:
        state: Market state string from classify_market_state().
        force: Optional variant key to force-override state selection.

    Returns a copy of the variant config dict.
    """
    if force and force in VARIANTS:
        return dict(VARIANTS[force])
    if state == "low_vol_bullish":
        return dict(VARIANTS["aggressive"])
    if state == "high_vol_bearish":
        return dict(VARIANTS["conservative"])
    return dict(VARIANTS["default"])


def get_active_variant(force: str = "") -> dict:
    """Top-level: classify market state → select variant.

    Called by OvernightPipeline at start of pre-market plan generation.
    Never raises — returns default variant on any failure.
    """
    try:
        state = classify_market_state()
        variant = select_variant(state, force)
        variant["_state"] = state
        return variant
    except Exception:
        return dict(VARIANTS["default"], **{"_state": "error"})


def variant_bc_rules_text(variant: dict) -> str:
    """Build the B/C rules string for LLM prompts from variant config."""
    b_pct = variant.get("source_b_max_pct", 20)
    b_stop = variant.get("source_b_stop_pct", -8)
    c_pct = variant.get("source_c_max_pct", 7.5)
    c_stop = variant.get("source_c_stop_pct", -5)
    return f"B类上限{b_pct:.0f}%止损{b_stop}%, C类上限{c_pct:.0f}%止损{c_stop}%。"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import argparse
    parser = argparse.ArgumentParser(description="Strategy variant selection")
    parser.add_argument("--force", "-f", default="", choices=["", "default", "conservative", "aggressive"],
                        help="Force a specific variant")
    parser.add_argument("--list", "-l", action="store_true", help="List all variants")
    parser.add_argument("--state", "-s", action="store_true", help="Show market state only")
    args = parser.parse_args()

    if args.list:
        for key, v in VARIANTS.items():
            print(f"\n[{key}] {v['name']}")
            for k, val in v.items():
                if k != "name":
                    print(f"  {k}: {val}")
        return

    if args.state:
        state = classify_market_state()
        print(f"Market state: {state}")
        return

    variant = get_active_variant(args.force)
    state = variant.pop("_state", "unknown")
    print(f"Market state: {state}")
    print(f"Active variant: {variant['name']}")
    print(f"Rules text: {variant_bc_rules_text(variant)}")
    print(f"\nFull config:\n{json.dumps(variant, ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    main()
