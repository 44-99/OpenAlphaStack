"""Stock news, announcements, and sentiment aggregation."""
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
    return os.path.join(CACHE_DIR, f"news_{name}.json")


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


def get_stock_news(code: str, limit: int = 10) -> dict:
    """Get recent news for a stock."""
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=code)

        if df.empty:
            return {"code": code, "news": [], "count": 0, "note": "No news found"}

        recent = df.head(limit)
        news_list = []
        for _, row in recent.iterrows():
            item = {
                "title": str(row.get("新闻标题", row.get("title", ""))),
                "time": str(row.get("发布时间", row.get("time", ""))),
                "source": str(row.get("文章来源", row.get("source", "")))[:50],
                "url": str(row.get("新闻链接", row.get("url", "")))[:200],
            }
            news_list.append(item)

        # Simple keyword-based sentiment
        positive_words = ["增长", "突破", "利好", "涨停", "中标", "回购", "增持", "扭亏", "新高"]
        negative_words = ["下降", "亏损", "减持", "跌停", "处罚", "警告", "退市", "暴雷", "诉讼"]

        pos_count = sum(
            1 for n in news_list
            if any(w in str(n.get("title", "")) for w in positive_words)
        )
        neg_count = sum(
            1 for n in news_list
            if any(w in str(n.get("title", "")) for w in negative_words)
        )

        sentiment = "positive" if pos_count > neg_count else (
            "negative" if neg_count > pos_count else "neutral"
        )

        result = {
            "code": code,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(news_list),
            "sentiment": sentiment,
            "positive_signals": pos_count,
            "negative_signals": neg_count,
            "news": news_list,
        }
        return result
    except Exception as e:
        return {"code": code, "error": friendly_error(code, e)}


def get_market_news(limit: int = 15) -> dict:
    """Get general market/金融 news."""
    try:
        import akshare as ak
        # Use stock_info_global_em for market-wide announcements
        all_news = []
        for code in ["600519", "000858", "300750", "601318"]:
            try:
                df = ak.stock_news_em(symbol=code)
                if not df.empty:
                    all_news.extend(df.head(3).to_dict("records"))
            except Exception:
                continue

        seen = set()
        unique = []
        for n in all_news:
            title = str(n.get("新闻标题", n.get("title", "")))
            if title and title not in seen:
                seen.add(title)
                unique.append({"title": title, "time": str(n.get("发布时间", ""))})

        result = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "count": len(unique[:limit]),
            "headlines": unique[:limit],
        }
        return result
    except Exception as e:
        return {"error": friendly_error("market", e)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Stock news and sentiment")
    parser.add_argument("target", nargs="?", help="Stock code (6 digits) or 'market' for headlines")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Number of news items")
    args = parser.parse_args()

    target = args.target or "market"
    cache_key = f"{target}_{args.limit}"

    cached = _read_cache(cache_key)
    if cached:
        print(json.dumps(cached, ensure_ascii=False, indent=2, default=str))
        if "error" in cached:
            sys.exit(1)
        return

    if target.lower() == "market":
        result = get_market_news(args.limit)
    else:
        result = get_stock_news(target, args.limit)

    _write_cache(cache_key, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
