from __future__ import annotations

import asyncio

from starlette.testclient import TestClient

from openalphastack.public_mcp_server import PUBLIC_TOOL_NAMES, app, public_mcp


WRITE_OR_PRIVATE_TOOLS = {
    "list_runs",
    "get_run_snapshot",
    "get_ledger_tail",
    "validate_paper_plan",
    "save_plan_draft",
    "publish_paper_plan",
}


def _tools():
    return asyncio.run(public_mcp.list_tools())


def test_public_mcp_exports_only_the_declared_read_only_surface():
    tools = _tools()
    names = {tool.name for tool in tools}

    assert names == set(PUBLIC_TOOL_NAMES)
    assert names.isdisjoint(WRITE_OR_PRIVATE_TOOLS)
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True


def test_public_mcp_input_schemas_are_bounded():
    tools = {tool.name: tool for tool in _tools()}

    quote_code = tools["stock_quote"].inputSchema["properties"]["code"]
    assert quote_code["pattern"] == r"^\d{6}$"

    news_limit = tools["stock_news"].inputSchema["properties"]["limit"]
    assert news_limit["minimum"] == 1
    assert news_limit["maximum"] == 20

    backtest_days = tools["run_rule_backtest"].inputSchema["properties"]["days"]
    assert backtest_days["minimum"] == 60
    assert backtest_days["maximum"] == 1000

    closes = tools["calculate_volatility"].inputSchema["properties"]["closes"]
    assert closes["minItems"] == 2
    assert closes["maxItems"] == 1000


def test_public_http_service_exposes_health_metadata_and_mcp_initialize():
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "openalphastack-test", "version": "1"},
        },
    }

    with TestClient(app) as client:
        health = client.get("/health")
        info = client.get("/")
        challenge = client.get("/.well-known/openai-apps-challenge")
        response = client.post(
            "/mcp",
            json=initialize,
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "openalphastack-public"}
    assert info.status_code == 200
    assert info.json()["read_only"] is True
    assert info.json()["stateless"] is True
    assert info.json()["tools"] == len(PUBLIC_TOOL_NAMES)
    assert challenge.status_code == 404
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["serverInfo"]["name"] == "openalphastack-public"


def test_public_demo_tool_returns_explicit_synthetic_metadata():
    result = asyncio.run(public_mcp.call_tool("read_demo_dataset", {"dataset": "market_overview"}))
    payload = result[1]

    assert payload["ok"] is True
    assert payload["meta"]["demo"] is True
    assert payload["meta"]["freshness"]["status"] == "static-demo"
