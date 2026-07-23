"""OpenAlphaStack stdio MCP server for Codex Desktop."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from openalphastack import agent_gateway
from openalphastack.tools.backtest import backtest_ma_cross, backtest_volume_breakout, fetch_hist
from openalphastack.tools.fundamental import get_fundamentals
from openalphastack.tools.news import get_market_news, get_stock_news
from openalphastack.tools.quote import get_market_overview, get_stock_quote
from openalphastack.tools.risk import calc_position_size, calc_volatility_metrics
from openalphastack.tools.screen import run_screen
from openalphastack.tools.technical import get_technical


mcp = FastMCP(
    "openalphastack",
    instructions=(
        "A paper-trading-only gateway. Read state before acting, validate every "
        "plan, save a draft first, and never claim that a plan was published "
        "unless publish_paper_plan returns published=true."
    ),
)


@mcp.tool()
def list_runs(mode: str = "paper", limit: int = 20) -> list[dict[str, Any]]:
    """List recent paper or backtest runs; live runs are intentionally hidden."""
    return agent_gateway.list_run_summaries(mode, limit)


@mcp.tool()
def market_overview() -> dict[str, Any]:
    """Read the current A-share market overview with source timestamps."""
    return get_market_overview()


@mcp.tool()
def stock_quote(code: str) -> dict[str, Any]:
    """Read a current quote for one six-digit A-share code."""
    return get_stock_quote(code)


@mcp.tool()
def stock_technical(code: str, indicator: str = "all") -> dict[str, Any]:
    """Calculate technical indicators from cached or fetched history."""
    return get_technical(code, indicator)


@mcp.tool()
def stock_fundamentals(code: str) -> dict[str, Any]:
    """Read fundamental and valuation fields for one stock."""
    return get_fundamentals(code)


@mcp.tool()
def stock_news(code: str, limit: int = 10) -> dict[str, Any]:
    """Read recent stock news; callers must preserve source time and failures."""
    return get_stock_news(code, limit)


@mcp.tool()
def market_news(limit: int = 15) -> dict[str, Any]:
    """Read recent market headlines."""
    return get_market_news(limit)


@mcp.tool()
def screen_candidates(strategy: str = "default", top_n: int = 15) -> dict[str, Any]:
    """Run a deterministic candidate screen using a named strategy."""
    return run_screen(strategy, top_n)


@mcp.tool()
def calculate_position_size(
    price: float,
    capital: float,
    position_limit_pct: float,
    max_drawdown_pct: float = 15.0,
) -> dict[str, Any]:
    """Calculate a lot-rounded position size without using an LLM."""
    return calc_position_size(
        price,
        capital,
        position_limit_pct / 100,
        max_drawdown_pct / 100,
    )


@mcp.tool()
def calculate_volatility(closes: list[float], lookback: int = 60) -> dict[str, Any]:
    """Calculate deterministic volatility metrics for a close-price series."""
    return calc_volatility_metrics(closes, lookback)


@mcp.tool()
def run_rule_backtest(code: str, strategy: str = "ma_cross", days: int = 500) -> dict[str, Any]:
    """Run a quick deterministic single-stock baseline backtest."""
    frame = fetch_hist(code, days)
    if frame.empty:
        return {"error": "no historical data", "code": code, "strategy": strategy}
    if strategy == "ma_cross":
        result = backtest_ma_cross(frame)
    elif strategy == "volume_breakout":
        result = backtest_volume_breakout(frame)
    else:
        return {"error": "unsupported strategy", "supported": ["ma_cross", "volume_breakout"]}
    return {"code": code, "strategy": strategy, "days": days, "result": result}


@mcp.tool()
def get_run_snapshot(run_id: str) -> dict[str, Any]:
    """Read state, plan, and the latest ledger entries for one run."""
    return agent_gateway.get_run_snapshot(run_id)


@mcp.tool()
def get_ledger_tail(run_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Read the most recent immutable ledger records for a run."""
    return agent_gateway.get_ledger_tail(run_id, limit)


@mcp.tool()
def validate_paper_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate a proposed plan against the paper-only schema and hard caps."""
    return agent_gateway.validate_paper_plan(plan)


@mcp.tool()
def save_plan_draft(run_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    """Save a non-executable Codex draft beside the active paper plan."""
    return agent_gateway.save_plan_draft(run_id, plan)


@mcp.tool()
def publish_paper_plan(
    run_id: str,
    plan: dict[str, Any],
    idempotency_key: str,
    expected_updated: str = "",
) -> dict[str, Any]:
    """Atomically publish a validated paper plan with optimistic concurrency."""
    return agent_gateway.publish_paper_plan(run_id, plan, idempotency_key, expected_updated)


def run() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
