"""Real-time stock quotes and market overview via Sina Finance API (primary)."""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import requests

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
CACHE_TTL = 300

SINA_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SINA_REFERER = "https://finance.sina.com.cn/"

MARKET_INDICES = {
    "000001": ("s_sh000001", "上证指数"),
    "399001": ("s_sz399001", "深证成指"),
    "399006": ("s_sz399006", "创业板指"),
    "000688": ("s_sh000688", "科创50"),
    "000300": ("s_sh000300", "沪深300"),
    "000905": ("s_sh000905", "中证500"),
}

STOCK_FIELDS = [
    "name", "open", "prev_close", "price", "high", "low", "bid", "ask",
    "volume", "amount",
]


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"quote_{name}.json")


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


def _sina_code(code: str) -> str:
    """Convert 600519 -> sh600519."""
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def get_stock_quote(code: str) -> dict:
    """Get real-time quote for a single stock via Sina Finance."""
    cached = _read_cache(code)
    if cached:
        return cached

    sina_code = _sina_code(code)
    try:
        resp = requests.get(
            f"https://hq.sinajs.cn/list={sina_code}",
            timeout=10,
            headers={"User-Agent": SINA_UA, "Referer": SINA_REFERER},
        )
        resp.encoding = "gbk"
        raw = resp.text

        if "=" not in raw or len(raw) < 50:
            return {"error": f"Stock not found: {code}"}

        data_str = raw.split("=", 1)[1].strip().strip('"')
        fields = data_str.split(",")
        if len(fields) < 10:
            return {"error": f"Incomplete data for {code}"}

        price = float(fields[3])
        prev_close = float(fields[2])
        change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0

        data = {
            "code": code,
            "name": fields[0],
            "price": price,
            "change_pct": change_pct,
            "change_amt": round(price - prev_close, 2),
            "open": float(fields[1]),
            "high": float(fields[4]),
            "low": float(fields[5]),
            "prev_close": prev_close,
            "volume": int(float(fields[8])),
            "amount": float(fields[9]),
            "bid": float(fields[6]) if fields[6] else None,
            "ask": float(fields[7]) if fields[7] else None,
            "date": fields[30] if len(fields) > 30 else "",
            "time": fields[31] if len(fields) > 31 else "",
            "source": "sina",
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        _write_cache(code, data)
        return data

    except (requests.RequestException, ValueError, IndexError) as e:
        return {"error": f"数据获取失败: {str(e)[:150]}", "code": code}


def get_market_overview() -> dict:
    """Get market overview: major indices via Sina."""
    cached = _read_cache("market")
    if cached:
        return cached

    sina_codes = ",".join(v[0] for v in MARKET_INDICES.values())
    try:
        resp = requests.get(
            f"https://hq.sinajs.cn/list={sina_codes}",
            timeout=10,
            headers={"User-Agent": SINA_UA, "Referer": SINA_REFERER},
        )
        resp.encoding = "gbk"
        indices = []
        for line in resp.text.strip().split("\n"):
            if "=" not in line:
                continue
            var_name, data_str = line.split("=", 1)
            sina_key = var_name.strip().split("_")[-1]
            fields = data_str.strip().strip('"').split(",")
            if len(fields) < 4:
                continue

            # Find matching index name
            index_name = sina_key
            clean_var = var_name.strip()
            for code, (skey, name) in MARKET_INDICES.items():
                if clean_var.endswith(skey):
                    index_name = name
                    break

            price = float(fields[1])
            change = float(fields[2]) if len(fields) > 2 else 0
            change_pct = float(fields[3]) if len(fields) > 3 else 0
            indices.append({
                "name": index_name,
                "price": price,
                "change": change,
                "change_pct": change_pct,
            })

        data = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "indices": indices,
            "source": "sina",
        }
        _write_cache("market", data)
        return data

    except (requests.RequestException, ValueError, IndexError) as e:
        return {"error": f"大盘数据获取失败: {str(e)[:150]}"}


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
