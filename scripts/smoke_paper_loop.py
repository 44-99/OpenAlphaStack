"""Exercise the real stdio MCP -> paper plan -> SQLite snapshot loop safely."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="Existing paper run id")
    parser.add_argument(
        "--command",
        default="openalphastack",
        help="OpenAlphaStack console executable used to start the stdio MCP server",
    )
    parser.add_argument("--cwd", default=str(Path.cwd()), help="Project root for the MCP process")
    return parser


def _envelope(result: Any, tool: str) -> dict[str, Any]:
    if result.isError:
        raise RuntimeError(f"MCP transport error from {tool}: {result.content}")
    payload = result.structuredContent
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP tool {tool} did not return structured content")
    if payload.get("ok") is not True:
        raise RuntimeError(f"MCP tool {tool} failed: {payload.get('error')}")
    return payload


async def _call(session: ClientSession, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return _envelope(await session.call_tool(tool, arguments or {}), tool)


async def run_smoke(run_id: str, command: str, cwd: str) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    server = StdioServerParameters(
        command=command,
        args=["mcp", "serve"],
        cwd=cwd,
        env=env,
        encoding="utf-8",
        encoding_error_handler="strict",
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            required = {
                "get_contracts",
                "read_demo_dataset",
                "get_run_snapshot",
                "publish_paper_plan",
                "get_ledger_tail",
            }
            missing = sorted(required - tool_names)
            if missing:
                raise RuntimeError(f"MCP server is missing required tools: {', '.join(missing)}")

            contracts = await _call(session, "get_contracts")
            demo = await _call(session, "read_demo_dataset", {"dataset": "market_overview"})
            before = await _call(session, "get_run_snapshot", {"run_id": run_id})
            before_snapshot = before["data"]

            today = date.today().isoformat()
            plan = {
                "plan_date": today,
                "market_bias": "neutral",
                "bias_confidence": 0,
                "bias_reasoning": (
                    "Manual end-to-end smoke test: observation only, no market conclusion, "
                    "no candidates and no orders."
                ),
                "position_cap_pct": 0,
                "provenance": {
                    "research_run_id": "manual-smoke-test",
                    "snapshot_ids": [run_id],
                    "latest_data_as_of": "",
                    "quality_status": "manual-observation-only",
                    "contains_demo_data": False,
                },
                "buy_candidates": [],
                "holding_adjustments": [],
            }
            key = f"manual-smoke-{today.replace('-', '')}-{uuid.uuid4().hex[:12]}"
            published = await _call(
                session,
                "publish_paper_plan",
                {
                    "run_id": run_id,
                    "plan": plan,
                    "idempotency_key": key,
                    "expected_updated": str(before_snapshot.get("plan", {}).get("updated") or ""),
                },
            )
            after = await _call(session, "get_run_snapshot", {"run_id": run_id})
            ledger = await _call(session, "get_ledger_tail", {"run_id": run_id, "limit": 20})
            after_snapshot = after["data"]

            if published["data"].get("published") is not True:
                raise RuntimeError(f"Plan was not published: {published['data']}")
            if after_snapshot["plan_revision"] <= before_snapshot["plan_revision"]:
                raise RuntimeError("Plan revision did not advance")
            if after_snapshot["plan"].get("idempotency_key") != key:
                raise RuntimeError("Published plan is not visible in the canonical snapshot")
            if ledger["data"]:
                raise RuntimeError("Observation-only smoke test unexpectedly created ledger events")

            return {
                "ok": True,
                "run_id": run_id,
                "mcp_tool_count": len(tool_names),
                "contract_version": contracts["schema_version"],
                "demo_guard": {
                    "demo": demo["meta"]["demo"],
                    "freshness": demo["meta"]["freshness"]["status"],
                },
                "publication": published["data"],
                "plan_revision_before": before_snapshot["plan_revision"],
                "plan_revision_after": after_snapshot["plan_revision"],
                "ledger_events": len(ledger["data"]),
                "observation_only": True,
            }


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(run_smoke(args.run_id, args.command, args.cwd))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
