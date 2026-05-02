"""Capital flow analysis: north-bound, institutional, large-order direction."""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from _http import friendly_error  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
CACHE_TTL = 300


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"flow_{name}.json")


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


def get_north_bound_flow() -> dict:
    """Get north-bound (沪港通/深港通) capital flow."""
    try:
        import akshare as ak
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
        today = df.iloc[-1] if not df.empty else None
        yesterday = df.iloc[-2] if len(df) > 1 else None

        data = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "today": {
                "net_flow": float(today["value"]) if today is not None else 0,
                "date": str(today["date"]) if today is not None else "",
            },
            "yesterday": {
                "net_flow": float(yesterday["value"]) if yesterday is not None else 0,
                "date": str(yesterday["date"]) if yesterday is not None else "",
            },
        }

        recent = df.tail(5)
        total_5d = float(recent["value"].sum())
        data["five_day_total"] = round(total_5d, 2)
        data["trend"] = "inflow_accelerating" if (
            data["today"]["net_flow"] > data["yesterday"]["net_flow"] > 0
        ) else "outflow_slowing" if (
            data["today"]["net_flow"] > data["yesterday"]["net_flow"]
        ) else "stable"

        return data
    except Exception as e:
        return {"error": friendly_error("north", e)}


def get_stock_flow(code: str) -> dict:
    """Get individual stock fund flow."""
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow(stock=code, market="sh" if code.startswith(("6", "9")) else "sz")

        if df.empty:
            return {"code": code, "error": "No flow data available"}

        recent = df.tail(5)
        latest = df.iloc[-1]

        main_net = float(latest.get("主力净流入", 0) or latest.get("主力净流入-净额", 0) or 0)
        super_large = float(latest.get("超大单净流入", 0) or latest.get("超大单净流入-净额", 0) or 0)
        large = float(latest.get("大单净流入", 0) or latest.get("大单净流入-净额", 0) or 0)
        medium = float(latest.get("中单净流入", 0) or latest.get("中单净流入-净额", 0) or 0)
        small = float(latest.get("小单净流入", 0) or latest.get("小单净流入-净额", 0) or 0)

        data = {
            "code": code,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "main_net_flow": main_net,
            "super_large_net": super_large,
            "large_net": large,
            "medium_net": medium,
            "small_net": small,
            "five_day_main_flow": round(float(recent[[c for c in recent.columns if "主力" in str(c)][0]].sum()) if any("主力" in str(c) for c in recent.columns) else 0, 2),
            "signal": "strong_buying" if main_net > 0 and super_large > 0 else (
                "buying" if main_net > 0 else (
                    "strong_selling" if main_net < 0 and super_large < 0 else
                    "selling" if main_net < 0 else "neutral"
                )
            ),
        }
        return data
    except Exception as e:
        return {"code": code, "error": friendly_error(code, e)}


def main():
    parser = argparse.ArgumentParser(description="Capital flow analysis for A-shares")
    parser.add_argument("target", nargs="?", help="Stock code (6 digits) or 'north' for north-bound flow")
    args = parser.parse_args()

    target = args.target or "north"
    cache_key = target

    cached = _read_cache(cache_key)
    if cached:
        print(json.dumps(cached, ensure_ascii=False, indent=2, default=str))
        if "error" in cached:
            sys.exit(1)
        return

    if target.lower() == "north":
        result = get_north_bound_flow()
    else:
        result = get_stock_flow(target)

    _write_cache(cache_key, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
