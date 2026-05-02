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


def get_fundamentals(code: str) -> dict:
    """Get fundamental indicators for a stock."""
    try:
        import akshare as ak

        # Real-time valuation from spot
        spot = ak.stock_zh_a_spot_em()
        stock = spot[spot["代码"] == code]
        if stock.empty:
            return {"error": f"Stock not found: {code}"}

        row = stock.iloc[0]
        result = {
            "code": code,
            "name": str(row["名称"]),
            "price": float(row["最新价"]),
            "pe": float(row.get("市盈率-动态", 0) or 0),
            "pb": float(row.get("市净率", 0) or 0),
            "total_market_cap": float(row.get("总市值", 0) or 0),
            "circulating_market_cap": float(row.get("流通市值", 0) or 0),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        # Financial indicators (quarterly)
        try:
            fin = ak.stock_financial_analysis_indicator(symbol=code)
            if not fin.empty:
                latest = fin.iloc[0]
                result["roe"] = float(latest.get("净资产收益率", 0) or 0)
                result["net_profit_growth"] = float(latest.get("净利润同比增长率", 0) or 0)
                result["revenue_growth"] = float(latest.get("营业收入同比增长率", 0) or 0)
                result["gross_margin"] = float(latest.get("销售毛利率", 0) or 0)
                result["net_margin"] = float(latest.get("销售净利率", 0) or 0)
                result["debt_ratio"] = float(latest.get("资产负债率", 0) or 0)
                result["eps"] = float(latest.get("每股收益", 0) or 0)
                result["bvps"] = float(latest.get("每股净资产", 0) or 0)
                result["report_period"] = str(latest.get("报告期", ""))
        except Exception as e:
            result["financial_detail_error"] = str(e)

        # Industry comparison
        try:
            industry = ak.stock_board_industry_name_spot_em()
            sector_name = str(stock.iloc[0].get("板块", "")) if "板块" in stock.columns else ""
            if sector_name:
                sector_row = industry[industry["板块名称"] == sector_name]
                if not sector_row.empty:
                    result["industry"] = {
                        "name": sector_name,
                        "sector_change_pct": float(sector_row.iloc[0].get("涨跌幅", 0)),
                        "sector_pe_avg": float(sector_row.iloc[0].get("市盈率", 0) or 0),
                    }
        except Exception:
            pass

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
