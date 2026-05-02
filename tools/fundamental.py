"""Fundamental data: PE, PB, ROE, revenue growth, industry comparison."""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from _http import friendly_error  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
CACHE_TTL = 3600  # fundamentals change slowly


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"fund_{name}.json")


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


def _sina_quote(code: str) -> dict:
    """Get name and price from Sina Finance real-time quote."""
    import requests
    sina_code = f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"
    resp = requests.get(
        f"https://hq.sinajs.cn/list={sina_code}",
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
    )
    resp.encoding = "gbk"
    raw = resp.text
    if "=" not in raw:
        return {}
    fields = raw.split("=", 1)[1].strip().strip('"').split(",")
    if len(fields) < 10:
        return {}
    return {"name": fields[0], "price": float(fields[3])}


def get_fundamentals(code: str) -> dict:
    """Get fundamental indicators for a stock."""
    import pandas as pd

    try:
        import akshare as ak

        quote = _sina_quote(code)
        if not quote:
            return {"error": f"Stock not found: {code}"}

        result = {
            "code": code,
            "name": quote["name"],
            "price": quote["price"],
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        # Financial indicators from akshare financial_abstract
        try:
            fin = ak.stock_financial_abstract(symbol=code)
            if not fin.empty:
                val_cols = [c for c in fin.columns if c not in ("选项", "指标")]
                indicators = {}
                for _, row in fin.iterrows():
                    key = str(row["指标"])
                    for col in val_cols:
                        v = row.get(col)
                        if pd.notna(v) and v != 0:
                            indicators[key] = float(v)
                            break

                result["roe"] = indicators.get("净资产收益率(ROE)")
                result["revenue_growth"] = indicators.get("营业总收入增长率")
                result["net_profit_growth"] = indicators.get("归属母公司净利润增长率")
                result["gross_margin"] = indicators.get("毛利率")
                result["net_margin"] = indicators.get("销售净利率")
                result["debt_ratio"] = indicators.get("资产负债率")
                result["eps"] = indicators.get("基本每股收益")
                result["bvps"] = indicators.get("每股净资产")
                result["report_period"] = str(val_cols[0]) if val_cols else ""

                if result["eps"] and result["eps"] > 0:
                    result["pe"] = round(quote["price"] / result["eps"], 2)
                if result["bvps"] and result["bvps"] > 0:
                    result["pb"] = round(quote["price"] / result["bvps"], 2)
        except Exception as e:
            result["financial_detail_error"] = str(e)

        return result

    except Exception as e:
        return {"error": friendly_error(code, e), "code": code}


def main():
    parser = argparse.ArgumentParser(description="Fundamental data for A-share stocks")
    parser.add_argument("code", help="Stock code (6 digits)")
    args = parser.parse_args()

    cached = _read_cache(args.code)
    if cached:
        print(json.dumps(cached, ensure_ascii=False, indent=2, default=str))
        return

    result = get_fundamentals(args.code)
    if "error" not in result:
        _write_cache(args.code, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
