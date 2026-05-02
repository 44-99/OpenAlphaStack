"""Multi-source data fallback for all CLI tools.

Priority chain per data type:
  Quotes: Sina → Tencent (qt.gtimg.cn) → akshare Sina spot → error
  K-line: Sina → akshare hist → error
  Financials: akshare financial_abstract → calculated from quote data
  Flow: akshare individual_fund_flow / hsgt_fund_flow_summary_em → error
  News: akshare stock_news_em → error
  Screen: Tencent batch API (qt.gtimg.cn, 200/req) → akshare Sina spot code list

Each function returns (data: dict, source: str, ok: bool).
Callers get clean JSON with source metadata.
"""
import os
import time
import json
import math
from datetime import datetime
from typing import Any

# Shared HTTP setup
os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")

SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}


def _safe_float(val: Any) -> float:
    try:
        v = float(val)
        return 0.0 if math.isnan(v) else v
    except (ValueError, TypeError):
        return 0.0


def _sina_code(code: str) -> str:
    return f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"


def _tencent_quote(code: str) -> dict | None:
    """Get real-time quote from Tencent Finance API. Returns dict or None."""
    import requests
    try:
        url = f"http://qt.gtimg.cn/q={_sina_code(code)}"
        resp = requests.get(url, timeout=10,
                          headers={"User-Agent": "Mozilla/5.0"})
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
            "open": float(fields[5]),
            "high": float(fields[33]),
            "low": float(fields[34]),
            "prev_close": prev_close,
            "volume": int(float(fields[6])),
            "amount": float(fields[37]) * 10000,  # 万元→元
            "change_pct": float(fields[32]),
            "change_amt": float(fields[31]),
            "turnover_rate": float(fields[38]) if fields[38] else 0,
            "pe": float(fields[39]) if fields[39] else None,
            "pb": float(fields[46]) if fields[46] else None,
            "volume_ratio": float(fields[49]) if fields[49] else 0,
            "high_limit": float(fields[47]) if fields[47] else None,
            "low_limit": float(fields[48]) if fields[48] else None,
            "amplitude": float(fields[43]) if fields[43] else 0,
            "source": "tencent",
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    except Exception:
        return None


# ── Quote ──────────────────────────────────────────────

def get_quote(code: str) -> tuple[dict, str]:
    """Get real-time quote. Returns (data_dict, source_name)."""
    import requests

    sources_tried = []

    # Source 1: Tencent Finance (fastest ~0.07s, 88 fields inc. PE/PB/turnover)
    try:
        q = _tencent_quote(code)
        if q:
            return q, "tencent"
        sources_tried.append("tencent:empty")
    except Exception as e:
        sources_tried.append(f"tencent:{type(e).__name__}")

    # Source 2: Sina Finance (~0.1s, 34 fields, basic OHLCV)
    try:
        resp = requests.get(
            f"https://hq.sinajs.cn/list={_sina_code(code)}",
            timeout=10,
            headers=SINA_HEADERS,
        )
        resp.encoding = "gbk"
        raw = resp.text
        if "=" in raw:
            fields = raw.split("=", 1)[1].strip().strip('"').split(",")
            if len(fields) >= 10:
                price = float(fields[3])
                prev_close = float(fields[2])
                return {
                    "code": code,
                    "name": fields[0],
                    "price": price,
                    "open": float(fields[1]),
                    "high": float(fields[4]),
                    "low": float(fields[5]),
                    "prev_close": prev_close,
                    "volume": int(float(fields[8])),
                    "amount": float(fields[9]),
                    "bid": float(fields[6]) if fields[6] else None,
                    "ask": float(fields[7]) if fields[7] else None,
                    "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                    "change_amt": round(price - prev_close, 2),
                    "date": fields[30] if len(fields) > 30 else "",
                    "time_field": fields[31] if len(fields) > 31 else "",
                    "source": "sina",
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }, "sina"
        sources_tried.append("sina:parse_error")
    except Exception as e:
        sources_tried.append(f"sina:{type(e).__name__}")

    # Source 3: akshare Sina spot (slow ~25s, last resort)
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        row = df[df["代码"].str.endswith(code)]
        if not row.empty:
            r = row.iloc[0]
            price = float(r["最新价"])
            prev = float(r.get("昨收", 0) or 0)
            return {
                "code": code,
                "name": str(r["名称"]),
                "price": price,
                "open": float(r.get("今开", 0) or 0),
                "high": float(r.get("最高", 0) or 0),
                "low": float(r.get("最低", 0) or 0),
                "prev_close": prev,
                "volume": int(float(r.get("成交量", 0) or 0)),
                "amount": float(r.get("成交额", 0) or 0),
                "change_pct": float(r.get("涨跌幅", 0) or 0),
                "source": "akshare_sina",
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }, "akshare_sina"
        sources_tried.append("akshare_sina:not_found")
    except Exception as e:
        sources_tried.append(f"akshare:{type(e).__name__}")

    return {"error": f"All sources failed for {code}", "sources_tried": sources_tried, "code": code}, "error"


# ── Historical K-line ──────────────────────────────────

def get_hist(code: str, days: int = 120) -> tuple:
    """Get daily historical OHLCV. Returns (DataFrame, source_name)."""
    import requests
    import pandas as pd

    sources_tried = []

    # Source 1: Sina K-line API
    try:
        url = (
            f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={_sina_code(code)}&scale=240&ma=no&datalen={days}"
        )
        resp = requests.get(url, timeout=15, headers=SINA_HEADERS)
        resp.encoding = "gbk"
        data = json.loads(resp.text)
        if data and isinstance(data, list):
            df = pd.DataFrame(data)
            df = df.rename(columns={"day": "date"})
            df["date"] = pd.to_datetime(df["date"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col])
            return df.sort_values("date").tail(days).reset_index(drop=True), "sina"
        sources_tried.append("sina:empty")
    except Exception as e:
        sources_tried.append(f"sina:{type(e).__name__}")

    # Source 2: akshare hist (may work on trading days)
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if not df.empty:
            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期").tail(days)
            return df.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume",
                "成交额": "amount", "涨跌幅": "change_pct",
            }).reset_index(drop=True), "akshare"
        sources_tried.append("akshare:empty")
    except Exception as e:
        sources_tried.append(f"akshare:{type(e).__name__}")

    return pd.DataFrame(), f"error:{','.join(sources_tried)}"


# ── Financials ─────────────────────────────────────────

def get_financials(code: str) -> tuple[dict, str]:
    """Get fundamental indicators. Returns (data_dict, source_name)."""
    import pandas as pd

    sources_tried = []

    try:
        import akshare as ak
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

            return {
                "roe": indicators.get("净资产收益率(ROE)"),
                "revenue_growth": indicators.get("营业总收入增长率"),
                "net_profit_growth": indicators.get("归属母公司净利润增长率"),
                "gross_margin": indicators.get("毛利率"),
                "net_margin": indicators.get("销售净利率"),
                "debt_ratio": indicators.get("资产负债率"),
                "eps": indicators.get("基本每股收益"),
                "bvps": indicators.get("每股净资产"),
                "report_period": str(val_cols[0]) if val_cols else "",
                "source": "akshare",
            }, "akshare"
    except Exception as e:
        sources_tried.append(f"akshare:{type(e).__name__}")

    return {"error": "All financial sources failed", "sources_tried": sources_tried}, "error"


# ── North-bound Flow ───────────────────────────────────

def get_north_flow() -> tuple[dict, str]:
    """Get north-bound capital flow snapshot."""
    sources_tried = []

    try:
        import akshare as ak
        df = ak.stock_hsgt_fund_flow_summary_em()
        if not df.empty:
            north = df[df["资金方向"] == "北向"]
            if not north.empty:
                total_net = sum(_safe_float(v) for v in north["资金净流入"])
                total_buy = sum(_safe_float(v) for v in north["成交净买额"])
                sh_row = north[north["板块"] == "沪股通"]
                sz_row = north[north["板块"] == "深股通"]
                sh_net = _safe_float(sh_row.iloc[0]["成交净买额"]) if not sh_row.empty else 0
                sz_net = _safe_float(sz_row.iloc[0]["成交净买额"]) if not sz_row.empty else 0
                status = int(df.iloc[0]["交易状态"])
                return {
                    "trade_date": str(df.iloc[0]["交易日"]),
                    "market_open": status not in (3,),
                    "north_net_flow": round(total_net, 2),
                    "north_net_buy": round(total_buy, 2),
                    "sh_net_buy": sh_net,
                    "sz_net_buy": sz_net,
                    "trend": "inflow" if total_net > 0 else ("outflow" if total_net < 0 else "flat"),
                    "source": "akshare",
                }, "akshare"
    except Exception as e:
        sources_tried.append(f"akshare:{type(e).__name__}")

    return {"error": "North-bound flow unavailable", "sources_tried": sources_tried}, "error"


# ── Individual Fund Flow ───────────────────────────────

def get_stock_flow(code: str) -> tuple[dict, str]:
    """Get individual stock capital flow."""
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow(
            stock=code,
            market="sh" if code.startswith(("6", "9")) else "sz",
        )
        if df.empty:
            return {"error": f"No flow data for {code}"}, "error"

        latest = df.iloc[-1]
        recent = df.tail(5)

        main_net = _safe_float(latest.get("主力净流入-净额", 0))
        super_large = _safe_float(latest.get("超大单净流入-净额", 0))
        large = _safe_float(latest.get("大单净流入-净额", 0))
        medium = _safe_float(latest.get("中单净流入-净额", 0))
        small = _safe_float(latest.get("小单净流入-净额", 0))

        signal = "strong_buying" if main_net > 0 and super_large > 0 else (
            "buying" if main_net > 0 else (
                "strong_selling" if main_net < 0 and super_large < 0 else
                "selling" if main_net < 0 else "neutral"
            )
        )

        flow_5d = sum(_safe_float(v) for v in recent["主力净流入-净额"])

        return {
            "code": code,
            "main_net_flow": main_net,
            "super_large_net": super_large,
            "large_net": large,
            "medium_net": medium,
            "small_net": small,
            "five_day_main_flow": round(flow_5d, 2),
            "signal": signal,
            "source": "akshare",
        }, "akshare"
    except Exception as e:
        return {"error": f"Flow data error for {code}: {str(e)[:120]}"}, "error"
