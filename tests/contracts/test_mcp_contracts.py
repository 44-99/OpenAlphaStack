from __future__ import annotations

import json
import asyncio

from openalphastack import agent_gateway
from openalphastack import mcp_server
from openalphastack.contracts import (
    MCP_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    RUN_SNAPSHOT_SCHEMA_VERSION,
    call,
    contract_catalog,
    success,
)
from openalphastack.demo_data import DEMO_DATASET_VERSION, list_datasets, read_dataset


def test_success_envelope_exposes_provenance_and_freshness():
    result = success(
        "quote",
        {"source": "fixture", "fetched_at": "2026-07-23T10:00:00+08:00", "price": 10},
        max_age_seconds=300,
    )

    assert result["schema_version"] == MCP_SCHEMA_VERSION
    assert result["ok"] is True
    assert result["data"]["price"] == 10
    assert result["meta"]["source"] == "fixture"
    assert result["meta"]["freshness"]["max_age_seconds"] == 300


def test_provider_errors_are_structured_and_do_not_leak_message():
    def broken():
        raise RuntimeError("provider secret endpoint failed")

    result = call("quote", broken)

    assert result["ok"] is False
    assert result["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert result["error"]["retryable"] is True
    assert "secret endpoint" not in json.dumps(result)


def test_demo_dataset_is_static_read_only_copy():
    first = read_dataset("market_overview")
    first["indices"].clear()
    second = read_dataset("market_overview")

    assert "market_overview" in list_datasets()
    assert "market_news" in list_datasets()
    assert second["indices"]
    assert second["breadth"]["advancers"] > 0
    assert second["source"] == DEMO_DATASET_VERSION


def test_demo_mcp_tool_marks_static_synthetic_provenance():
    result = mcp_server.read_demo_dataset("stock_quote")

    assert result["ok"] is True
    assert result["meta"]["demo"] is True
    assert result["meta"]["source"] == DEMO_DATASET_VERSION
    assert result["meta"]["freshness"]["status"] == "static-demo"


def test_invalid_demo_dataset_returns_structured_error():
    result = mcp_server.read_demo_dataset("not-a-dataset")

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"


def test_market_mcp_wrapper_adds_versioned_envelope(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "get_market_overview",
        lambda: {"source": "fixture", "time": "2026-07-23T10:00:00+08:00", "indices": []},
    )

    result = mcp_server.market_overview()

    assert result["schema_version"] == MCP_SCHEMA_VERSION
    assert result["ok"] is True
    assert result["meta"]["source"] == "fixture"


def test_fastmcp_advertises_contract_demo_and_run_resources():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    resources = asyncio.run(mcp_server.mcp.list_resources())
    templates = asyncio.run(mcp_server.mcp.list_resource_templates())

    assert {tool.name for tool in tools} >= {"get_contracts", "read_demo_dataset", "publish_paper_plan"}
    assert {str(resource.uri) for resource in resources} >= {
        "openalphastack://contracts/v1",
        "openalphastack://demo/catalog",
    }
    assert {str(template.uriTemplate) for template in templates} >= {
        "openalphastack://demo/{dataset}",
        "openalphastack://runs/{run_id}/snapshot",
        "openalphastack://runs/{run_id}/ledger",
    }


def test_contract_catalog_contains_plan_and_snapshot_schemas():
    catalog = contract_catalog()

    assert PLAN_SCHEMA_VERSION in catalog["contracts"]
    assert RUN_SNAPSHOT_SCHEMA_VERSION in catalog["contracts"]


def test_snapshot_and_published_plan_include_contract_versions(monkeypatch, tmp_path):
    patch = type("Run", (), {"run_dir": str(tmp_path), "mode": "paper"})()
    monkeypatch.setattr(agent_gateway.run_registry, "get_run", lambda _run_id: patch)
    plan = {
        "plan_date": "2026-07-23",
        "market_bias": "neutral",
        "position_cap_pct": 0,
        "buy_candidates": [],
        "holding_adjustments": [],
    }

    published = agent_gateway.publish_paper_plan("paper_demo", plan, "demo-key-20260723")
    stored = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    snapshot = agent_gateway.get_run_snapshot("paper_demo")

    assert published["published"] is True
    assert stored["schema_version"] == PLAN_SCHEMA_VERSION
    assert snapshot["schema_version"] == RUN_SNAPSHOT_SCHEMA_VERSION
