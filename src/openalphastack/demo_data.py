"""Small deterministic, read-only dataset for offline MCP and Skill demos."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEMO_DATASET_VERSION = "openalphastack.demo/2026-07-22"
DEMO_AS_OF = "2026-07-22T15:00:00+08:00"

_DATA: dict[str, Any] = {
    "market_overview": {
        "time": DEMO_AS_OF,
        "source": DEMO_DATASET_VERSION,
        "indices": [
            {"name": "上证指数", "price": 3581.86, "change_pct": 0.42},
            {"name": "深证成指", "price": 11152.31, "change_pct": 0.71},
            {"name": "创业板指", "price": 2316.44, "change_pct": 0.96},
        ],
        "breadth": {"advancers": 3120, "decliners": 1740, "unchanged": 215},
        "turnover_cny_billion": 1128.0,
        "sector_leaders": [
            {"name": "Demo Technology", "change_pct": 2.1},
            {"name": "Demo Consumer", "change_pct": 1.3},
        ],
        "note": "Synthetic values for workflow verification; not investment data.",
    },
    "market_news": {
        "time": DEMO_AS_OF,
        "source": DEMO_DATASET_VERSION,
        "items": [
            {
                "title": "Demo policy briefing emphasizes stable market expectations",
                "published_at": "2026-07-22T09:30:00+08:00",
                "source": "demo-wire",
            },
            {
                "title": "Demo technology sector reports higher synthetic turnover",
                "published_at": "2026-07-22T13:20:00+08:00",
                "source": "demo-wire",
            },
        ],
        "note": "Synthetic headlines for workflow verification; not real news.",
    },
    "stock_quote": {
        "code": "600519",
        "name": "Demo Stock",
        "price": 1500.0,
        "open": 1488.0,
        "high": 1512.0,
        "low": 1481.0,
        "prev_close": 1490.0,
        "change_pct": 0.67,
        "volume": 123456,
        "source": DEMO_DATASET_VERSION,
        "fetched_at": DEMO_AS_OF,
    },
    "stock_technical": {
        "code": "600519",
        "name": "Demo Stock",
        "time": DEMO_AS_OF,
        "source": DEMO_DATASET_VERSION,
        "ma": {"MA5": 1492.0, "MA10": 1485.0, "MA20": 1470.0, "price": 1500.0},
        "macd": {"DIF": 8.2, "DEA": 6.7, "BAR": 3.0, "signal": "bullish"},
        "rsi": {"RSI": 58.0, "period": 14, "zone": "neutral"},
        "volume_price": {"volume_ratio": 1.12, "signal": "accumulation"},
    },
    "stock_fundamentals": {
        "code": "600519",
        "name": "Demo Stock",
        "time": DEMO_AS_OF,
        "source": DEMO_DATASET_VERSION,
        "pe": 24.5,
        "pb": 7.8,
        "roe": 31.2,
        "revenue_growth": 12.0,
        "note": "Synthetic values for schema and workflow tests.",
    },
    "stock_news": {
        "code": "600519",
        "time": DEMO_AS_OF,
        "source": DEMO_DATASET_VERSION,
        "items": [
            {
                "title": "Demo company publishes a routine operating update",
                "published_at": "2026-07-22T10:00:00+08:00",
                "source": "demo-wire",
            }
        ],
    },
    "screen_candidates": {
        "strategy": "demo",
        "time": DEMO_AS_OF,
        "source": DEMO_DATASET_VERSION,
        "candidates": [
            {"code": "600519", "name": "Demo Stock", "score": 78.0, "reason": "demo trend and liquidity"},
            {"code": "000001", "name": "Demo Bank", "score": 71.0, "reason": "demo value baseline"},
        ],
    },
    "rule_backtest": {
        "code": "600519",
        "strategy": "ma_cross",
        "source": DEMO_DATASET_VERSION,
        "as_of": DEMO_AS_OF,
        "result": {
            "total_return_pct": 3.2,
            "benchmark_return_pct": 4.1,
            "max_drawdown_pct": -5.8,
            "trade_count": 8,
            "note": "Synthetic result; verifies reporting and is not performance evidence.",
        },
    },
}

# Dashboard-specific presentation fixtures live beside the MCP Demo catalog so
# synthetic account, plan, and market examples have one ownership boundary.
_DASHBOARD_STATE: dict[str, Any] = {
    "run_id": "demo_run",
    "total_asset": 103280.0,
    "cash": 72180.0,
    "position_value": 31100.0,
    "day_pnl": 3280.0,
    "day_return_pct": 3.28,
    "trade_count": 3,
    "win_count": 2,
    "positions": {
        "300913": {
            "shares": 1000,
            "avg_cost": 30.8,
            "current_price": 31.1,
            "stop_loss": 29.4,
            "strategy": "AI计划突破",
            "unrealized_pnl": 300.0,
        },
    },
    "engine_meta": {"mode": "demo", "status": "demo"},
    "data_time": "2026-06-04 10:30:00",
}

_DASHBOARD_PLAN: dict[str, Any] = {
    "updated": "2026-06-04T08:45:00",
    "updated_by": "demo",
    "market_bias": "谨慎偏多",
    "bias_confidence": 68,
    "bias_reasoning": "指数在关键均线附近企稳，优先选择有量能确认的弹性标的。",
    "buy_candidates": [
        {
            "code": "300913",
            "strategy_type": "突破回踩",
            "entry_min": 30.2,
            "entry_max": 31.2,
            "stop_loss": 29.4,
            "take_profit": 34.6,
            "valid_until": "2026-06-04",
            "position_pct": 20,
            "reasoning": "计划等待回踩不破入场区间，上沿突破后由成交量确认。",
        },
        {
            "code": "000001",
            "name": "上证指数",
            "strategy_type": "指数观察",
            "entry_min": 10.4,
            "entry_max": 10.8,
            "stop_loss": 10.1,
            "take_profit": 11.5,
            "valid_until": "2026-06-04",
            "position_pct": 10,
            "reasoning": "作为大盘联动观察样例，不代表真实交易建议。",
        },
    ],
    "rules": {
        "max_single_position_pct": 25,
        "max_total_position_pct": 80,
        "stop_loss_mode": "hard",
    },
}

_DASHBOARD_LEDGER: list[dict[str, Any]] = [
    {
        "seq": 3,
        "time": "2026-06-04 10:12:00",
        "decision": "buy",
        "symbol": "300913",
        "price": 31.1,
        "shares": 1000,
        "strategy": "突破回踩",
        "reasoning": "回踩计划区间后放量重新站上分时均线。",
        "stop_loss": 29.4,
        "take_profit": 34.6,
        "avg_cost": 30.8,
    },
    {
        "seq": 2,
        "time": "2026-06-04 09:48:00",
        "decision": "sell",
        "symbol": "300913",
        "price": 32.2,
        "shares": 500,
        "strategy": "T+0降成本",
        "reasoning": "冲高接近分时压力，先兑现一半日内仓。",
        "stop_loss": 29.4,
        "take_profit": 34.6,
        "avg_cost": 30.8,
    },
    {
        "seq": 1,
        "time": "2026-06-04 09:35:00",
        "decision": "buy",
        "symbol": "300913",
        "price": 30.8,
        "shares": 1500,
        "strategy": "计划入场",
        "reasoning": "价格进入计划区间，风控校验通过。",
        "stop_loss": 29.4,
        "take_profit": 34.6,
        "avg_cost": 30.8,
    },
]


def list_datasets() -> list[str]:
    return sorted(_DATA)


def read_dataset(dataset: str) -> Any:
    if dataset not in _DATA:
        raise ValueError(f"unknown demo dataset: {dataset}; choose from {', '.join(list_datasets())}")
    return deepcopy(_DATA[dataset])


def dashboard_state() -> dict[str, Any]:
    return deepcopy(_DASHBOARD_STATE)


def dashboard_plan() -> dict[str, Any]:
    return deepcopy(_DASHBOARD_PLAN)


def dashboard_ledger(limit: int = 50, code: str = "") -> list[dict[str, Any]]:
    rows = deepcopy(_DASHBOARD_LEDGER)
    if code:
        rows = [row for row in rows if row.get("symbol") == code or row.get("code") == code]
    return rows[: max(0, int(limit))]
