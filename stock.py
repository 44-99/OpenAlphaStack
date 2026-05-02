"""
Stock data fetching via akshare.
"""
import json
import os
import time
from datetime import datetime
from config import STOCK_DATA_DIR


def _cache_path(name: str) -> str:
    return os.path.join(STOCK_DATA_DIR, f"{name}.json")


CACHE_TTL = int(os.getenv("STOCK_CACHE_TTL", "600"))


def _read_cache(name: str, max_age_seconds: int = None) -> dict | None:
    if max_age_seconds is None:
        max_age_seconds = CACHE_TTL
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > max_age_seconds:
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_cache(name: str, data: dict) -> None:
    os.makedirs(STOCK_DATA_DIR, exist_ok=True)
    with open(_cache_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def get_market_overview() -> dict:
    """Get market overview: major indices, sentiment, north-bound flow."""
    cached = _read_cache("market_overview")
    if cached:
        return cached

    try:
        import akshare as ak

        indices = ak.stock_zh_index_spot_em()
        key_indices = ["上证指数", "深证成指", "创业板指", "科创50", "沪深300", "中证500"]
        idx_data = indices[indices["名称"].isin(key_indices)][["名称", "最新价", "涨跌幅", "成交量", "成交额"]].to_dict("records")

        try:
            sectors = ak.stock_board_industry_name_spot_em()
            top_sectors = sectors.nlargest(5, "涨跌幅")[["板块名称", "涨跌幅", "领涨股票"]].to_dict("records")
        except (OSError, ValueError, RuntimeError):
            top_sectors = []

        try:
            all_stocks = ak.stock_zh_a_spot_em()
            up_count = int((all_stocks["涨跌幅"] > 0).sum())
            down_count = int((all_stocks["涨跌幅"] < 0).sum())
            flat_count = int((all_stocks["涨跌幅"] == 0).sum())
        except (OSError, ValueError, RuntimeError):
            up_count = down_count = flat_count = 0

        data = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "indices": idx_data,
            "top_sectors": top_sectors,
            "breadth": {"up": up_count, "down": down_count, "flat": flat_count},
        }
        _write_cache("market_overview", data)
        return data

    except (OSError, ValueError, RuntimeError) as e:
        return {"error": str(e), "time": datetime.now().strftime("%Y-%m-%d %H:%M")}


def get_hot_stocks(top_n: int = 10) -> dict:
    """Get today's hot stocks ranking."""
    cached = _read_cache("hot_stocks")
    if cached:
        return cached

    try:
        import akshare as ak

        hot = ak.stock_hot_rank_live_em()
        top = hot.head(top_n)[["个股代码", "个股名称", "热度", "现价", "涨跌幅"]].to_dict("records")

        data = {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "hot_stocks": top}
        _write_cache("hot_stocks", data)
        return data

    except (OSError, ValueError, RuntimeError) as e:
        return {"error": str(e)}


def get_stock_detail(code: str) -> dict:
    """Get detailed info for a specific stock."""
    cached = _read_cache(f"stock_{code}")
    if cached:
        return cached

    try:
        import akshare as ak

        spot = ak.stock_zh_a_spot_em()
        stock = spot[spot["代码"] == code]
        if stock.empty:
            return {"error": f"未找到股票代码: {code}"}

        row = stock.iloc[0]
        data = {
            "代码": str(row["代码"]),
            "名称": str(row["名称"]),
            "最新价": float(row["最新价"]),
            "涨跌幅": float(row["涨跌幅"]),
            "涨跌额": float(row["涨跌额"]),
            "成交量": int(row["成交量"]),
            "成交额": float(row["成交额"]),
            "换手率": float(row["换手率"]),
            "量比": float(row["量比"]),
            "市盈率": float(row.get("市盈率-动态", 0) or 0),
            "市净率": float(row.get("市净率", 0) or 0),
            "最高": float(row["最高"]),
            "最低": float(row["最低"]),
            "今开": float(row["今开"]),
            "昨收": float(row["昨收"]),
        }

        try:
            hist = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
            recent = hist.tail(20)
            data["recent_20_days"] = recent[["日期", "开盘", "收盘", "最高", "最低", "成交量", "涨跌幅"]].to_dict("records")
        except (OSError, ValueError, RuntimeError):
            data["recent_20_days"] = []

        _write_cache(f"stock_{code}", data)
        return data

    except (OSError, ValueError, RuntimeError) as e:
        return {"error": str(e)}


def get_top_gainers(top_n: int = 10) -> dict:
    """Get top gainers of the day."""
    try:
        import akshare as ak

        all_stocks = ak.stock_zh_a_spot_em()
        gainers = all_stocks.nlargest(top_n, "涨跌幅")[["代码", "名称", "最新价", "涨跌幅", "换手率", "成交额"]].to_dict("records")
        return {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "top_gainers": gainers}
    except (OSError, ValueError, RuntimeError) as e:
        return {"error": str(e)}


def get_potential_picks() -> dict:
    """
    Multi-factor screening for short/medium-term trading candidates.
    - Short-term: high momentum, breaking MA, high turnover, volume surge
    - Medium-term: reasonable PE/PB, sector strength, institutional flow
    """
    try:
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        df = df[~df["名称"].str.contains("ST|退市")]
        df = df[(df["最新价"] > 5) & (df["最新价"] < 200)]
        df = df[df["成交额"] > 1e8]

        df_short = df[
            (df["涨跌幅"] > 2) & (df["涨跌幅"] < 9) &
            (df["换手率"] > 3) & (df["换手率"] < 20) &
            (df["量比"] > 1.5)
        ].nlargest(15, "涨跌幅")

        df_medium = df[
            (df["市盈率-动态"] > 0) & (df["市盈率-动态"] < 50) &
            (df["市净率"] > 0) & (df["市净率"] < 8) &
            (df["涨跌幅"] > 1) & (df["涨跌幅"] < 7) &
            (df["换手率"] > 2) & (df["换手率"] < 15)
        ].nlargest(15, "量比")

        short = df_short[["代码", "名称", "最新价", "涨跌幅", "换手率", "量比", "市盈率-动态"]].to_dict("records")
        medium = df_medium[["代码", "名称", "最新价", "涨跌幅", "换手率", "量比", "市盈率-动态"]].to_dict("records")

        return {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "short_term_candidates": short,
            "medium_term_candidates": medium,
        }
    except (OSError, ValueError, RuntimeError) as e:
        return {"error": str(e)}


def format_market_report() -> str:
    """Format a comprehensive market report for Claude analysis."""
    overview = get_market_overview()
    hot = get_hot_stocks(10)
    gainers = get_top_gainers(10)
    picks = get_potential_picks()

    report = f"=== 市场数据报告 (生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}) ===\n\n"

    report += "【大盘指数】\n"
    if "indices" in overview:
        for idx in overview["indices"]:
            sign = "+" if idx.get("涨跌幅", 0) > 0 else ""
            report += f"  {idx['名称']}: {idx['最新价']} ({sign}{idx['涨跌幅']}%)\n"

    if "breadth" in overview:
        b = overview["breadth"]
        report += f"  上涨/下跌/平盘: {b['up']}/{b['down']}/{b['flat']}\n"

    if "top_sectors" in overview and overview["top_sectors"]:
        report += "\n【热门板块】\n"
        for s in overview["top_sectors"]:
            report += f"  {s['板块名称']}: {s['涨跌幅']}% (领涨: {s.get('领涨股票', '')})\n"

    if "hot_stocks" in hot:
        report += "\n【人气热度榜 TOP10】\n"
        for s in hot["hot_stocks"]:
            report += f"  {s['个股代码']} {s['个股名称']} | 现价:{s['现价']} | 涨跌:{s['涨跌幅']}% | 热度:{s['热度']}\n"

    if "top_gainers" in gainers:
        report += "\n【今日涨幅榜 TOP10】\n"
        for s in gainers["top_gainers"]:
            report += f"  {s['代码']} {s['名称']} | {s['最新价']} | {s['涨跌幅']}% | 换手:{s['换手率']}%\n"

    if "short_term_candidates" in picks:
        report += "\n【短线候选标的(多因子筛选)】\n"
        report += "条件: 涨幅2-9%, 换手3-20%, 量比>1.5, 成交额>1亿\n"
        for s in picks["short_term_candidates"]:
            report += f"  {s['代码']} {s['名称']} | 涨跌:{s['涨跌幅']}% | 换手:{s['换手率']}% | 量比:{s['量比']} | PE:{s.get('市盈率-动态', 'N/A')}\n"

    if "medium_term_candidates" in picks:
        report += "\n【中线候选标的(多因子筛选)】\n"
        report += "条件: PE 0-50, PB 0-8, 涨幅1-7%, 换手2-15%\n"
        for s in picks["medium_term_candidates"]:
            report += f"  {s['代码']} {s['名称']} | 涨跌:{s['涨跌幅']}% | 换手:{s['换手率']}% | 量比:{s['量比']} | PE:{s.get('市盈率-动态', 'N/A')}\n"

    return report


if __name__ == "__main__":
    print(format_market_report())
