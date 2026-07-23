from __future__ import annotations

import json
from types import SimpleNamespace

from openalphastack import agent_gateway
from openalphastack.engine.plan import PlanManager


def test_mcp_published_plan_hydrates_complete_engine_contract(monkeypatch, tmp_path):
    record = SimpleNamespace(run_id="paper_contract", run_dir=str(tmp_path), mode="paper")
    monkeypatch.setattr(agent_gateway.run_registry, "get_run", lambda _run_id: record)
    manager = PlanManager(str(tmp_path))
    plan = {
        "plan_date": "2026-07-23",
        "market_bias": "neutral",
        "position_cap_pct": 20,
        "buy_candidates": [],
        "holding_adjustments": [],
    }

    result = agent_gateway.publish_paper_plan(
        "paper_contract",
        plan,
        "contract-20260723",
        expected_updated=manager.load()["updated"],
    )
    assert result["published"] is True
    assert manager.refresh_external() is True

    loaded = manager.load()
    persisted = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert loaded["rules"]["max_single_position_pct"] == 20.0
    assert loaded["rules"]["max_total_position_pct"] == 20
    assert persisted == loaded
