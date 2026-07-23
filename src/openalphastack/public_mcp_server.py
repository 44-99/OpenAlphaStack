"""Stateless, read-only HTTP MCP surface for a separately hosted public service."""

from __future__ import annotations

import os
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from openalphastack.contracts import MCP_SCHEMA_VERSION, call, success
from openalphastack.demo_data import (
    DEMO_AS_OF,
    DEMO_DATASET_VERSION,
    list_datasets,
    read_dataset,
)
from openalphastack.tools.backtest import (
    backtest_ma_cross,
    backtest_volume_breakout,
    fetch_hist,
)
from openalphastack.tools.fundamental import get_fundamentals
from openalphastack.tools.news import get_market_news, get_stock_news
from openalphastack.tools.quote import fetch_market_overview, fetch_stock_quote
from openalphastack.tools.risk import calc_position_size, calc_volatility_metrics
from openalphastack.tools.technical import calculate_technical


PUBLIC_TOOL_NAMES = (
    "get_public_capabilities",
    "list_demo_datasets",
    "read_demo_dataset",
    "market_overview",
    "stock_quote",
    "stock_technical",
    "stock_fundamentals",
    "stock_news",
    "market_news",
    "calculate_position_size",
    "calculate_volatility",
    "run_rule_backtest",
)

StockCode = Annotated[
    str,
    Field(pattern=r"^\d{6}$", description="Six-digit Shanghai or Shenzhen A-share code."),
]
NewsLimit = Annotated[int, Field(ge=1, le=20)]
DemoDataset = Literal[
    "market_news",
    "market_overview",
    "rule_backtest",
    "screen_candidates",
    "stock_fundamentals",
    "stock_news",
    "stock_quote",
    "stock_technical",
]
TechnicalIndicator = Literal["ma", "macd", "rsi", "kdj", "bollinger", "volume", "all"]
BacktestStrategy = Literal["ma_cross", "volume_breakout"]


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _transport_security() -> TransportSecuritySettings:
    hosts = ["127.0.0.1:*", "localhost:*", "testserver"]
    hosts.extend(_csv_env("OPENALPHASTACK_PUBLIC_HOSTS"))
    render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if render_host:
        hosts.append(render_host)

    origins = ["https://44-99.github.io"]
    origins.extend(_csv_env("OPENALPHASTACK_PUBLIC_ORIGINS"))
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(hosts)),
        allowed_origins=list(dict.fromkeys(origins)),
    )


READ_ONLY_LOCAL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
READ_ONLY_OPEN_WORLD = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


public_mcp = FastMCP(
    "openalphastack-public",
    instructions=(
        "A stateless, read-only A-share research service. It cannot access local runs, "
        "portfolios, ledgers, drafts, paper plans, broker accounts, or arbitrary files. "
        "Every result uses the openalphastack.mcp/v1 envelope. Check ok and preserve "
        "meta.source, meta.as_of, meta.freshness, and meta.demo in downstream answers. "
        "Synthetic demo data is never investment data."
    ),
    website_url="https://44-99.github.io/OpenAlphaStack/",
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security(),
)


@public_mcp.tool(
    title="Describe the public OpenAlphaStack boundary",
    annotations=READ_ONLY_LOCAL,
)
def get_public_capabilities() -> dict[str, Any]:
    """List public tools and the capabilities intentionally excluded from this service."""
    return success(
        "get_public_capabilities",
        {
            "service": "openalphastack-public",
            "schema_version": MCP_SCHEMA_VERSION,
            "transport": "streamable-http",
            "tools": list(PUBLIC_TOOL_NAMES),
            "boundary": {
                "read_only": True,
                "stateless": True,
                "paper_only_project": True,
                "excluded": [
                    "local run discovery",
                    "run snapshots and ledgers",
                    "plan drafts and publication",
                    "broker connectivity and live orders",
                    "arbitrary file or shell access",
                ],
            },
            "support_url": "https://44-99.github.io/OpenAlphaStack/support.html",
        },
        source="openalphastack-public",
    )


@public_mcp.tool(title="List deterministic demo datasets", annotations=READ_ONLY_LOCAL)
def list_demo_datasets() -> dict[str, Any]:
    """List the synthetic datasets available for deterministic workflow checks."""
    return success(
        "list_demo_datasets",
        {"version": DEMO_DATASET_VERSION, "datasets": list_datasets()},
        source=DEMO_DATASET_VERSION,
        as_of=DEMO_AS_OF,
        demo=True,
    )


@public_mcp.tool(title="Read a deterministic demo dataset", annotations=READ_ONLY_LOCAL)
def read_demo_dataset(dataset: DemoDataset = "market_overview") -> dict[str, Any]:
    """Read synthetic data for protocol checks; the result is never real market data."""
    return success(
        "read_demo_dataset",
        read_dataset(dataset),
        source=DEMO_DATASET_VERSION,
        as_of=DEMO_AS_OF,
        demo=True,
    )


@public_mcp.tool(title="Read current A-share market indices", annotations=READ_ONLY_OPEN_WORLD)
def market_overview() -> dict[str, Any]:
    """Read current major A-share indices without accessing local trading state."""
    return call("market_overview", fetch_market_overview, max_age_seconds=300)


@public_mcp.tool(title="Read an A-share quote", annotations=READ_ONLY_OPEN_WORLD)
def stock_quote(code: StockCode) -> dict[str, Any]:
    """Read a current quote for one six-digit A-share code."""
    return call("stock_quote", lambda: fetch_stock_quote(code), max_age_seconds=300)


@public_mcp.tool(title="Calculate A-share technical indicators", annotations=READ_ONLY_OPEN_WORLD)
def stock_technical(
    code: StockCode,
    indicator: TechnicalIndicator = "all",
) -> dict[str, Any]:
    """Calculate indicators from fetched price history without using local run data."""
    return call(
        "stock_technical",
        lambda: calculate_technical(code, indicator),
        max_age_seconds=600,
        source="sina-history",
    )


@public_mcp.tool(title="Read A-share fundamentals", annotations=READ_ONLY_OPEN_WORLD)
def stock_fundamentals(code: StockCode) -> dict[str, Any]:
    """Read fundamental and valuation fields for one stock."""

    def operation() -> dict[str, Any]:
        data = get_fundamentals(code)
        data.pop("financial_detail_error", None)
        return data

    return call(
        "stock_fundamentals",
        operation,
        max_age_seconds=86400,
        source="akshare+sina",
    )


@public_mcp.tool(title="Read recent A-share stock news", annotations=READ_ONLY_OPEN_WORLD)
def stock_news(code: StockCode, limit: NewsLimit = 10) -> dict[str, Any]:
    """Read recent stock news with a bounded result count."""
    return call(
        "stock_news",
        lambda: get_stock_news(code, limit),
        max_age_seconds=86400,
        source="akshare",
    )


@public_mcp.tool(title="Read recent A-share market news", annotations=READ_ONLY_OPEN_WORLD)
def market_news(limit: NewsLimit = 15) -> dict[str, Any]:
    """Read recent market headlines with a bounded result count."""
    return call(
        "market_news",
        lambda: get_market_news(limit),
        max_age_seconds=86400,
        source="akshare",
    )


@public_mcp.tool(title="Calculate an A-share position size", annotations=READ_ONLY_LOCAL)
def calculate_position_size(
    price: Annotated[float, Field(gt=0, le=1_000_000)],
    capital: Annotated[float, Field(gt=0, le=1_000_000_000_000)],
    position_limit_pct: Annotated[float, Field(gt=0, le=25)],
    max_drawdown_pct: Annotated[float, Field(ge=0, le=100)] = 15.0,
) -> dict[str, Any]:
    """Calculate lot-rounded position size without an LLM or persistent state."""
    return call(
        "calculate_position_size",
        lambda: calc_position_size(
            price,
            capital,
            position_limit_pct / 100,
            max_drawdown_pct / 100,
        ),
    )


@public_mcp.tool(title="Calculate volatility metrics", annotations=READ_ONLY_LOCAL)
def calculate_volatility(
    closes: Annotated[list[Annotated[float, Field(gt=0)]], Field(min_length=2, max_length=1000)],
    lookback: Annotated[int, Field(ge=2, le=252)] = 60,
) -> dict[str, Any]:
    """Calculate deterministic volatility metrics from a bounded close-price series."""
    return call("calculate_volatility", lambda: calc_volatility_metrics(closes, lookback))


def _run_backtest(code: str, strategy: BacktestStrategy, days: int) -> dict[str, Any]:
    frame = fetch_hist(code, days)
    if frame.empty:
        return {"error": "no historical data", "code": code, "strategy": strategy}
    result = (
        backtest_ma_cross(frame)
        if strategy == "ma_cross"
        else backtest_volume_breakout(frame)
    )
    return {
        "code": code,
        "strategy": strategy,
        "days": days,
        "result": result,
        "source": "sina-history",
    }


@public_mcp.tool(title="Run a bounded rule backtest", annotations=READ_ONLY_OPEN_WORLD)
def run_rule_backtest(
    code: StockCode,
    strategy: BacktestStrategy = "ma_cross",
    days: Annotated[int, Field(ge=60, le=1000)] = 500,
) -> dict[str, Any]:
    """Run one deterministic baseline backtest over bounded fetched history."""
    return call(
        "run_rule_backtest",
        lambda: _run_backtest(code, strategy, days),
        max_age_seconds=86400,
    )


async def service_info(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "service": "openalphastack-public",
            "status": "ok",
            "mcp": "/mcp",
            "transport": "streamable-http",
            "read_only": True,
            "stateless": True,
            "tools": len(PUBLIC_TOOL_NAMES),
            "privacy": "https://44-99.github.io/OpenAlphaStack/privacy.html",
            "terms": "https://44-99.github.io/OpenAlphaStack/terms.html",
            "support": "https://44-99.github.io/OpenAlphaStack/support.html",
        }
    )


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "openalphastack-public"})


async def openai_apps_challenge(_: Request) -> PlainTextResponse:
    token = os.getenv("OPENAI_APPS_CHALLENGE_TOKEN", "").strip()
    if not token:
        return PlainTextResponse("challenge token is not configured", status_code=404)
    return PlainTextResponse(token)


app = public_mcp.streamable_http_app()
app.add_route("/", service_info, methods=["GET"])
app.add_route("/health", health, methods=["GET"])
app.add_route(
    "/.well-known/openai-apps-challenge",
    openai_apps_challenge,
    methods=["GET"],
)


def run() -> None:
    """Run the public ASGI service; TLS is terminated by the hosting platform."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - exercised by CLI packaging checks
        raise RuntimeError('Install the public extra: pip install -e ".[public]"') from exc

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run()
