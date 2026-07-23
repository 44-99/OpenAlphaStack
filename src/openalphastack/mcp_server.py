"""Versioned OpenAlphaStack stdio MCP server for Codex Desktop."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from openalphastack import agent_gateway
from openalphastack.contracts import call, contract_catalog, success
from openalphastack.demo_data import DEMO_AS_OF, DEMO_DATASET_VERSION, list_datasets, read_dataset
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
        "A paper-trading-only gateway. Every tool returns a versioned envelope: "
        "check ok before reading data, preserve meta.source/as_of/freshness, and "
        "never claim publication unless publish_paper_plan returns data.published=true. "
        "Use read_demo_dataset for deterministic offline demonstrations."
    ),
)


@mcp.tool()
def get_contracts() -> dict[str, Any]:
    """Return JSON schemas and versions for public MCP payloads."""
    return success("get_contracts", contract_catalog(), source="openalphastack-contracts")


@mcp.tool()
def read_demo_dataset(dataset: str = "market_overview") -> dict[str, Any]:
    """Read deterministic synthetic data; never reads or mutates a trading run."""
    try:
        data = read_dataset(dataset)
    except ValueError as exc:
        return call("read_demo_dataset", lambda: (_ for _ in ()).throw(exc))
    return success(
        "read_demo_dataset",
        data,
        source=DEMO_DATASET_VERSION,
        as_of=DEMO_AS_OF,
        demo=True,
    )


@mcp.tool()
def list_runs(mode: str = "paper", limit: int = 20) -> dict[str, Any]:
    """List recent paper or backtest runs; live runs are intentionally hidden."""
    return call("list_runs", lambda: agent_gateway.list_run_summaries(mode, limit))


@mcp.tool()
def market_overview() -> dict[str, Any]:
    """Read current A-share indices with explicit provenance and freshness."""
    return call("market_overview", get_market_overview, max_age_seconds=300)


@mcp.tool()
def stock_quote(code: str) -> dict[str, Any]:
    """Read a current quote for one six-digit A-share code."""
    return call("stock_quote", lambda: get_stock_quote(code), max_age_seconds=300)


@mcp.tool()
def stock_technical(code: str, indicator: str = "all") -> dict[str, Any]:
    """Calculate indicators from cached or fetched history."""
    return call(
        "stock_technical",
        lambda: get_technical(code, indicator),
        max_age_seconds=600,
        source="sina-history",
    )


@mcp.tool()
def stock_fundamentals(code: str) -> dict[str, Any]:
    """Read fundamental and valuation fields for one stock."""
    return call(
        "stock_fundamentals",
        lambda: get_fundamentals(code),
        max_age_seconds=86400,
        source="akshare+sina",
    )


@mcp.tool()
def stock_news(code: str, limit: int = 10) -> dict[str, Any]:
    """Read recent stock news and preserve source time."""
    return call(
        "stock_news",
        lambda: get_stock_news(code, limit),
        max_age_seconds=86400,
        source="akshare",
    )


@mcp.tool()
def market_news(limit: int = 15) -> dict[str, Any]:
    """Read recent market headlines."""
    return call("market_news", lambda: get_market_news(limit), max_age_seconds=86400, source="akshare")


@mcp.tool()
def screen_candidates(strategy: str = "default", top_n: int = 15) -> dict[str, Any]:
    """Run a deterministic candidate screen using a named strategy."""
    return call(
        "screen_candidates",
        lambda: run_screen(strategy, top_n),
        max_age_seconds=900,
        source="openalphastack-screen",
    )


@mcp.tool()
def calculate_position_size(
    price: float,
    capital: float,
    position_limit_pct: float,
    max_drawdown_pct: float = 15.0,
) -> dict[str, Any]:
    """Calculate lot-rounded position size without an LLM."""
    return call(
        "calculate_position_size",
        lambda: calc_position_size(price, capital, position_limit_pct / 100, max_drawdown_pct / 100),
    )


@mcp.tool()
def calculate_volatility(closes: list[float], lookback: int = 60) -> dict[str, Any]:
    """Calculate deterministic volatility metrics for close prices."""
    return call("calculate_volatility", lambda: calc_volatility_metrics(closes, lookback))


def _run_backtest(code: str, strategy: str, days: int) -> dict[str, Any]:
    frame = fetch_hist(code, days)
    if frame.empty:
        return {"error": "no historical data", "code": code, "strategy": strategy}
    if strategy == "ma_cross":
        result = backtest_ma_cross(frame)
    elif strategy == "volume_breakout":
        result = backtest_volume_breakout(frame)
    else:
        raise ValueError("strategy must be ma_cross or volume_breakout")
    return {"code": code, "strategy": strategy, "days": days, "result": result, "source": "sina-history"}


@mcp.tool()
def run_rule_backtest(code: str, strategy: str = "ma_cross", days: int = 500) -> dict[str, Any]:
    """Run a deterministic single-stock baseline backtest."""
    return call("run_rule_backtest", lambda: _run_backtest(code, strategy, days), max_age_seconds=86400)


@mcp.tool()
def get_run_snapshot(run_id: str) -> dict[str, Any]:
    """Read versioned state, plan and latest ledger records for one run."""
    return call("get_run_snapshot", lambda: agent_gateway.get_run_snapshot(run_id))


@mcp.tool()
def get_ledger_tail(run_id: str, limit: int = 100) -> dict[str, Any]:
    """Read recent immutable ledger records for a run."""
    return call("get_ledger_tail", lambda: agent_gateway.get_ledger_tail(run_id, limit))


@mcp.tool()
def validate_paper_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Preview hard publication checks for manual authoring; automated flows skip this tool."""
    return call("validate_paper_plan", lambda: agent_gateway.validate_paper_plan(plan))


@mcp.tool()
def save_plan_draft(run_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    """Optionally save a non-executable draft for explicit human review."""
    return call("save_plan_draft", lambda: agent_gateway.save_plan_draft(run_id, plan))


@mcp.tool()
def publish_paper_plan(
    run_id: str,
    plan: dict[str, Any],
    idempotency_key: str,
    expected_updated: str = "",
) -> dict[str, Any]:
    """Validate and atomically publish a paper plan in one automated operation."""
    return call(
        "publish_paper_plan",
        lambda: agent_gateway.publish_paper_plan(run_id, plan, idempotency_key, expected_updated),
    )


@mcp.resource("openalphastack://contracts/v1")
def contracts_resource() -> str:
    return json.dumps(contract_catalog(), ensure_ascii=False, indent=2)


@mcp.resource("openalphastack://demo/catalog")
def demo_catalog_resource() -> str:
    return json.dumps({"version": DEMO_DATASET_VERSION, "datasets": list_datasets()}, ensure_ascii=False, indent=2)


@mcp.resource("openalphastack://demo/{dataset}")
def demo_dataset_resource(dataset: str) -> str:
    return json.dumps(read_dataset(dataset), ensure_ascii=False, indent=2)


@mcp.resource("openalphastack://runs/{run_id}/snapshot")
def run_snapshot_resource(run_id: str) -> str:
    return json.dumps(agent_gateway.get_run_snapshot(run_id), ensure_ascii=False, indent=2)


@mcp.resource("openalphastack://runs/{run_id}/ledger")
def run_ledger_resource(run_id: str) -> str:
    return json.dumps(agent_gateway.get_ledger_tail(run_id), ensure_ascii=False, indent=2)


def run() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
