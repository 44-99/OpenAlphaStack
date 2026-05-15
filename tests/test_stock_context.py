from __future__ import annotations

from alphaclaude.app import main as app_main


def test_extract_stock_code_before_chinese_text():
    assert app_main._extract_stock_codes("分析一下600584这支股票") == ["600584"]


def test_stock_context_uses_package_tools_for_code_before_chinese(monkeypatch):
    import stock
    from alphaclaude.tools import quote, technical

    monkeypatch.setattr(stock, "get_market_overview", lambda: {"error": "skip"})
    monkeypatch.setattr(
        quote,
        "get_stock_quote",
        lambda code: {
            "code": code,
            "name": "长电科技",
            "fetched_at": "2026-05-14 16:29",
            "price": 54.31,
            "change_pct": -5.53,
            "turnover_rate": 9.75,
            "volume_ratio": 0.96,
            "pe": 58.82,
            "pb": 3.38,
            "open": 57.79,
            "high": 57.79,
            "low": 54.28,
            "volume": 1744515,
            "amount": 9730730000.0,
        },
    )
    monkeypatch.setattr(
        technical,
        "get_technical",
        lambda code, indicator="all": {
            "code": code,
            "ma": {"MA5": 54.72, "MA10": 50.56, "MA20": 47.54, "vs_ma5": -0.75},
            "macd": {"signal": "bullish", "crossover": "none"},
            "volume_price": {"signal": "accumulation", "volume_ratio": 1.6},
        },
    )

    text, ok = app_main._fetch_stock_context("分析一下600584这支股票，今天可以买吗")

    assert ok is True
    assert "长电科技 600584" in text
    assert "MA5/MA10/MA20: 54.72/50.56/47.54" in text
