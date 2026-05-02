"""Pluggable multi-factor stock screening via Tencent Finance API (primary) + akshare Sina spot (code list).

Data flow:
  1. akshare stock_zh_a_spot() → full code list (cached 3600s, stock list changes slowly)
  2. Tencent qt.gtimg.cn batch (200/req) → 88-field real-time data for all stocks
  3. Strategy filters applied → top-N results returned
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
STRATEGIES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "strategies")
CACHE_TTL = 300
CODE_LIST_TTL = 3600

TENCENT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TENCENT_BATCH = 200

# Tencent Finance API → strategy column name mapping
TENCENT_MAP = {
    1: "名称",
    2: "代码",
    3: "最新价",
    4: "昨收",
    5: "今开",
    6: "成交量",
    31: "涨跌额",
    32: "涨跌幅",
    33: "最高",
    34: "最低",
    37: "成交额",       # 万元 → converted to 元 below
    38: "换手率",
    39: "市盈率-动态",
    43: "振幅",
    44: "流通市值",
    46: "市净率",
    49: "量比",
}

NUMERIC_COLS = ["最新价", "涨跌幅", "涨跌额", "最高", "最低", "昨收", "今开",
                "换手率", "市盈率-动态", "市净率", "量比", "振幅", "成交量", "成交额"]


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"screen_{name}.json")


def _read_cache(name: str, ttl: int = CACHE_TTL) -> dict | None:
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > ttl:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_cache(name: str, data: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def load_strategy(name: str) -> dict:
    for ext in (".json",):
        path = os.path.join(STRATEGIES_DIR, f"{name}{ext}")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    return {}


def list_strategies() -> list[str]:
    if not os.path.isdir(STRATEGIES_DIR):
        return []
    return sorted(fname.replace(".json", "") for fname in os.listdir(STRATEGIES_DIR)
                  if fname.endswith(".json"))


def _get_all_codes() -> list[str]:
    """Get all A-share stock codes (sh/sz only) via akshare Sina backend. Cached 1hr."""
    cached = _read_cache("code_list", ttl=CODE_LIST_TTL)
    if cached:
        return cached.get("codes", [])

    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        codes = [c for c in df["代码"].tolist() if c.startswith(("sh", "sz"))]
        _write_cache("code_list", {"codes": codes, "count": len(codes),
                    "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        return codes
    except Exception:
        # Fallback: try loading stale cache
        path = _cache_path("code_list")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("codes", [])
        return []


def _fetch_tencent_batch(codes: list[str]) -> list[dict]:
    """Fetch data for a batch of stocks from Tencent Finance API."""
    url = f"http://qt.gtimg.cn/q={','.join(codes)}"
    try:
        resp = requests.get(url, timeout=30,
                          headers={"User-Agent": TENCENT_UA})
        resp.encoding = "gbk"
    except requests.RequestException:
        return []

    rows = []
    for line in resp.text.strip().split("\n"):
        if '="' not in line:
            continue
        try:
            data_str = line.split('="', 1)[1].strip().strip('"')
            fields = data_str.split("~")
            if len(fields) < 50:
                continue
            row = {}
            for idx, col in TENCENT_MAP.items():
                row[col] = fields[idx] if idx < len(fields) else ""
            rows.append(row)
        except (IndexError, ValueError):
            continue
    return rows


def _build_dataframe(codes: list[str]) -> pd.DataFrame:
    """Fetch all stocks from Tencent API in batches and build a DataFrame."""
    all_rows = []
    total = len(codes)
    for i in range(0, total, TENCENT_BATCH):
        batch = codes[i:i + TENCENT_BATCH]
        rows = _fetch_tencent_batch(batch)
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    if df.empty:
        return df

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 成交额: 万元 → 元 (strategy configs expect 元) — must convert to numeric first
    if "成交额" in df.columns:
        df["成交额"] = df["成交额"] * 10000

    return df


def apply_filters(df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
    """Apply a list of filter conditions to the dataframe."""
    mask = pd.Series([True] * len(df), index=df.index)
    for f in filters:
        for cond in f.get("conditions", [f]):
            col = cond["column"]
            op = cond["op"]
            val = cond["value"]
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
                mask &= ~df[col].astype(str).str.contains(val, na=False)
    return df[mask]


def run_screen(strategy_name: str = "default", top_n: int = 15) -> dict:
    """Run a screening strategy against the full A-share market."""
    strategy = load_strategy(strategy_name)
    if not strategy:
        return {"error": f"Strategy not found: {strategy_name}. Available: {list_strategies()}"}

    try:
        codes = _get_all_codes()
        if not codes:
            return {"error": "无法获取股票列表（代码列表为空）", "strategy": strategy_name}

        df = _build_dataframe(codes)
        if df.empty:
            return {"error": "无法获取行情数据（腾讯API返回为空）", "strategy": strategy_name}

        for filter_block in strategy.get("filters", []):
            df = apply_filters(df, filter_block.get("conditions", [filter_block]))

        sort_by = strategy.get("sort_by", "涨跌幅")
        ascending = strategy.get("sort_ascending", False)
        if sort_by in df.columns:
            df = df.sort_values(sort_by, ascending=ascending)

        output_cols = strategy.get("output_columns",
            ["代码", "名称", "最新价", "涨跌幅", "换手率", "量比", "市盈率-动态", "成交额"])
        available_cols = [c for c in output_cols if c in df.columns]
        top = df.head(top_n)[available_cols].to_dict("records")

        # Format floats for clean JSON output
        for row in top:
            for k, v in row.items():
                if isinstance(v, float):
                    row[k] = round(v, 2)

        return {
            "strategy": strategy_name,
            "description": strategy.get("description", ""),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total_matched": len(df),
            "top_n": len(top),
            "results": top,
        }

    except Exception as e:
        return {"error": f"筛选异常: {str(e)[:200]}", "strategy": strategy_name}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
