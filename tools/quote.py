"""Real-time stock quotes and market overview via akshare."""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from _http import friendly_error  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
CACHE_TTL = 600


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"{name}.json")


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


def get_stock_quote(code: str) -> dict:
    """Get real-time quote for a single stock."""
    cached = _read_cache(f"quote_{code}")
    if cached:
        return cached

    try:
        import akshare as ak
        spot = ak.stock_zh_a_spot_em()
        stock = spot[spot["代码"] == code]
        if stock.empty:
            return {"error": f"Stock not found: {code}"}

        row = stock.iloc[0]
        data = {
            "code": str(row["代码"]),
            "name": str(row["名称"]),
            "price": float(row["最新价"]),
            "change_pct": float(row["涨跌幅"]),
            "change_amt": float(row["涨跌额"]),
            "volume": int(row["成交量"]),
            "turnover": float(row["成交额"]),
            "turnover_rate": float(row["换手率"]),
            "volume_ratio": float(row["量比"]),
            "pe": float(row.get("市盈率-动态", 0) or 0),
            "pb": float(row.get("市净率", 0) or 0),
            "high": float(row["最高"]),
            "low": float(row["最低"]),
            "open": float(row["今开"]),
            "prev_close": float(row["昨收"]),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        _write_cache(f"quote_{code}", data)
        return data

    except Exception as e:
        return {"error": friendly_error(code, e), "code": code}


def get_market_overview() -> dict:
    """Get market overview: major indices, breadth, top sectors."""
    cached = _read_cache("market_overview")
    if cached:
        return cached

    try:
        import akshare as ak
        indices = ak.stock_zh_index_spot_em()
        key = ["上证指数", "深证成指", "创业板指", "科创50", "沪深300", "中证500"]
        idx_data = (
            indices[indices["名称"].isin(key)][["名称", "最新价", "涨跌幅", "成交量", "成交额"]]
            .to_dict("records")
        )

        try:
            all_stocks = ak.stock_zh_a_spot_em()
            up = int((all_stocks["涨跌幅"] > 0).sum())
            down = int((all_stocks["涨跌幅"] < 0).sum())
            flat = int((all_stocks["涨跌幅"] == 0).sum())
        except Exception:
            up = down = flat = 0

        data = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "indices": idx_data,
            "breadth": {"up": up, "down": down, "flat": flat},
        }
        _write_cache("market_overview", data)
        return data

    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Real-time stock quote and market overview")
    parser.add_argument("code", nargs="?", help="Stock code (6 digits), or 'market' for overview")
    args = parser.parse_args()

    if not args.code or args.code.lower() == "market":
        result = get_market_overview()
    else:
        result = get_stock_quote(args.code)

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
