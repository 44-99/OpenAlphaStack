"""Real-time stock quotes and market overview. Primary: Tencent (qt.gtimg.cn, 88 fields)."""
import argparse
import json
import os
from alphaclaude.paths import PROJECT_ROOT
import sys
import time
from datetime import datetime

import requests
from alphaclaude.tools._http import get_session, retry_get
from alphaclaude.tools._registry import tool_meta

tool_meta(
    name="quote",
    category="行情",
    description="个股实时行情或大盘指数",
    usage="python -m alphaclaude.tools.quote <code> 或 market",
    scenario="获取价格、涨跌幅、换手率、量比、PE/PB",
)

CACHE_DIR = os.path.join(str(PROJECT_ROOT), "data", "cache")
CACHE_TTL = 300

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

MARKET_INDICES = {
    "000001": ("s_sh000001", "上证指数"),
    "399001": ("s_sz399001", "深证成指"),
    "399006": ("s_sz399006", "创业板指"),
    "000688": ("s_sh000688", "科创50"),
    "000300": ("s_sh000300", "沪深300"),
    "000905": ("s_sh000905", "中证500"),
}


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


def _prefixed_code(code: str) -> str:
    return f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"


def _fetch_tencent(code: str) -> dict | None:
    """Fetch quote from Tencent Finance API (88 fields, ~0.07s)."""
    try:
        url = f"http://qt.gtimg.cn/q={_prefixed_code(code)}"
        session = get_session()
        session.headers.update({"User-Agent": UA})
        resp = retry_get(session, url, timeout=10)
        resp.encoding = "gbk"
        line = resp.text.strip()
        if '="' not in line:
            return None
        fields = line.split('="', 1)[1].strip().strip('"').split("~")
        if len(fields) < 50:
            return None
        price = float(fields[3])
        prev_close = float(fields[4])
        return {
            "code": code,
            "name": fields[1],
            "price": price,
            "change_pct": float(fields[32]),
            "change_amt": float(fields[31]),
            "open": float(fields[5]),
            "high": float(fields[33]),
            "low": float(fields[34]),
            "prev_close": prev_close,
            "volume": int(float(fields[6])),
            "amount": float(fields[37]) * 10000,  # 万元→元
            "turnover_rate": float(fields[38]) if fields[38] else 0,
            "volume_ratio": float(fields[49]) if fields[49] else 0,
            "pe": float(fields[39]) if fields[39] else None,
            "pb": float(fields[46]) if fields[46] else None,
            "amplitude": float(fields[43]) if fields[43] else 0,
            "market_cap": float(fields[45]) if fields[45] else None,  # 亿元
            "high_limit": float(fields[47]) if fields[47] else None,
            "low_limit": float(fields[48]) if fields[48] else None,
            "source": "tencent",
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    except Exception:
        return None


def _fetch_sina(code: str) -> dict | None:
    """Fetch quote from Sina Finance API (34 fields, ~0.1s)."""
    try:
        session = get_session()
        session.headers.update({"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
        resp = retry_get(session, f"https://hq.sinajs.cn/list={_prefixed_code(code)}", timeout=10)
        resp.encoding = "gbk"
        raw = resp.text
        if "=" not in raw or len(raw) < 50:
            return None
        fields = raw.split("=", 1)[1].strip().strip('"').split(",")
        if len(fields) < 10:
            return None
        price = float(fields[3])
        prev_close = float(fields[2])
        return {
            "code": code,
            "name": fields[0],
            "price": price,
            "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
            "change_amt": round(price - prev_close, 2),
            "open": float(fields[1]),
            "high": float(fields[4]),
            "low": float(fields[5]),
            "prev_close": prev_close,
            "volume": int(float(fields[8])),
            "amount": float(fields[9]),
            "date": fields[30] if len(fields) > 30 else "",
            "time_field": fields[31] if len(fields) > 31 else "",
            "source": "sina",
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    except Exception:
        return None


def get_stock_quote(code: str) -> dict:
    """Get real-time quote. Primary: Tencent → Sina → akshare Sina spot."""
    cached = _read_cache(code)
    if cached:
        return cached

    # Source 1: Tencent (fastest, most complete)
    data = _fetch_tencent(code)
    if data:
        _write_cache(code, data)
        return data

    # Source 2: Sina (fast, basic fields)
    data = _fetch_sina(code)
    if data:
        _write_cache(code, data)
        return data

    # Source 3: akshare Sina spot (slow, last resort)
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        row = df[df["代码"].str.endswith(code)]
        if not row.empty:
            r = row.iloc[0]
            data = {
                "code": code,
                "name": str(r["名称"]),
                "price": float(r["最新价"]),
                "open": float(r.get("今开", 0) or 0),
                "high": float(r.get("最高", 0) or 0),
                "low": float(r.get("最低", 0) or 0),
                "prev_close": float(r.get("昨收", 0) or 0),
                "volume": int(float(r.get("成交量", 0) or 0)),
                "amount": float(r.get("成交额", 0) or 0),
                "change_pct": float(r.get("涨跌幅", 0) or 0),
                "source": "akshare_sina",
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            _write_cache(code, data)
            return data
    except Exception:
        pass

    return {"error": f"所有数据源均无法获取 {code}", "code": code}


def get_market_overview() -> dict:
    """Get market overview: major indices via Sina batch API."""
    cached = _read_cache("market")
    if cached:
        return cached

    sina_codes = ",".join(v[0] for v in MARKET_INDICES.values())
    try:
        session = get_session()
        session.headers.update({"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})
        resp = retry_get(session, f"https://hq.sinajs.cn/list={sina_codes}", timeout=10)
        resp.encoding = "gbk"
        indices = []
        for line in resp.text.strip().split("\n"):
            if "=" not in line:
                continue
            var_name, data_str = line.split("=", 1)
            fields = data_str.strip().strip('"').split(",")
            if len(fields) < 4:
                continue
            clean_var = var_name.strip()
            index_name = ""
            for code, (skey, name) in MARKET_INDICES.items():
                if clean_var.endswith(skey):
                    index_name = name
                    break
            indices.append({
                "name": index_name or clean_var,
                "price": float(fields[1]),
                "change": float(fields[2]) if len(fields) > 2 else 0,
                "change_pct": float(fields[3]) if len(fields) > 3 else 0,
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
