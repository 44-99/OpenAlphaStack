"""Pluggable multi-factor stock screening with strategy config files."""
import argparse
import json
import os
import sys
import time
from datetime import datetime

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
STRATEGIES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "strategies")
CACHE_TTL = 300


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"screen_{name}.json")


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


def load_strategy(name: str) -> dict:
    """Load a strategy config from strategies/<name>.json or .yaml."""
    for ext in (".json",):
        path = os.path.join(STRATEGIES_DIR, f"{name}{ext}")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    return {}


def list_strategies() -> list[str]:
    """List available strategy names."""
    if not os.path.isdir(STRATEGIES_DIR):
        return []
    names = []
    for fname in os.listdir(STRATEGIES_DIR):
        if fname.endswith(".json"):
            names.append(fname.replace(".json", ""))
    return sorted(names)


def apply_filters(df, filters: list[dict]) -> list[dict]:
    """Apply a list of filter conditions to the dataframe."""
    import pandas as pd
    mask = pd.Series([True] * len(df), index=df.index)
    for f in filters:
        col = f["column"]
        op = f["op"]
        val = f["value"]
        if col not in df.columns:
            continue
        if op == "gt":
            mask &= df[col] > val
        elif op == "gte":
            mask &= df[col] >= val
        elif op == "lt":
            mask &= df[col] < val
        elif op == "lte":
            mask &= df[col] <= val
        elif op == "between":
            mask &= df[col].between(val[0], val[1])
        elif op == "not_contains":
            mask &= ~df[col].str.contains(val, na=False)
    return df[mask]


def run_screen(strategy_name: str = "default", top_n: int = 15) -> dict:
    """Run a screening strategy."""
    try:
        import akshare as ak
        import pandas as pd

        strategy = load_strategy(strategy_name)
        if not strategy:
            return {"error": f"Strategy not found: {strategy_name}. Available: {list_strategies()}"}

        df = ak.stock_zh_a_spot_em()

        # Apply filters from strategy
        for filter_block in strategy.get("filters", []):
            df = apply_filters(df, filter_block.get("conditions", [filter_block]))

        # Get top N by sort column
        sort_by = strategy.get("sort_by", "涨跌幅")
        ascending = strategy.get("sort_ascending", False)
        if sort_by in df.columns:
            df = df.sort_values(sort_by, ascending=ascending)

        output_cols = strategy.get("output_columns",
            ["代码", "名称", "最新价", "涨跌幅", "换手率", "量比", "市盈率-动态", "成交额"])

        available_cols = [c for c in output_cols if c in df.columns]
        top = df.head(top_n)[available_cols].to_dict("records")

        result = {
            "strategy": strategy_name,
            "description": strategy.get("description", ""),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_matched": len(df),
            "top_n": len(top),
            "results": top,
        }
        return result

    except Exception as e:
        return {"error": str(e), "strategy": strategy_name}


def main():
    parser = argparse.ArgumentParser(description="Multi-factor stock screening")
    parser.add_argument("--strategy", "-s", default="default", help="Strategy name from strategies/")
    parser.add_argument("--list", "-l", action="store_true", help="List available strategies")
    parser.add_argument("--top", "-n", type=int, default=15, help="Number of results")
    args = parser.parse_args()

    if args.list:
        strategies = list_strategies()
        print(json.dumps({"strategies": strategies}, ensure_ascii=False, indent=2))
        return

    cache_key = f"{args.strategy}_{args.top}"
    cached = _read_cache(cache_key)
    if cached:
        print(json.dumps(cached, ensure_ascii=False, indent=2, default=str))
        if "error" in cached:
            sys.exit(1)
        return

    result = run_screen(args.strategy, args.top)
    _write_cache(cache_key, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
