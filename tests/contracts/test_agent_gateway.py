from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openalphastack import agent_gateway
from openalphastack.engine import plan as plan_module
from openalphastack.engine.plan import PlanManager


def valid_plan() -> dict:
    return {
        "plan_date": "2026-07-23",
        "market_bias": "neutral",
        "bias_reasoning": "test plan",
        "position_cap_pct": 20,
        "buy_candidates": [
            {
                "code": "600519",
                "entry_max": 100,
                "stop_loss_pct": -5,
                "take_profit_pct": 8,
                "position_pct": 10,
            }
        ],
        "holding_adjustments": [],
    }


def patch_run(monkeypatch, run_dir, mode="paper"):
    record = SimpleNamespace(run_id="paper_test", run_dir=str(run_dir), mode=mode)
    monkeypatch.setattr(agent_gateway.run_registry, "get_run", lambda _run_id: record)
    return record


def test_validate_paper_plan_rejects_cap_overflow_and_bad_code():
    plan = valid_plan()
    plan["position_cap_pct"] = 5
    plan["buy_candidates"][0]["code"] = "BAD"

    result = agent_gateway.validate_paper_plan(plan)

    assert result["valid"] is False
    assert any("six-digit" in error for error in result["errors"])
    assert any("exceeds" in error for error in result["errors"])


def test_publish_treats_model_authored_metadata_as_non_blocking(monkeypatch, tmp_path):
    patch_run(monkeypatch, tmp_path)
    plan = valid_plan()
    plan["buy_candidates"][0].pop("stop_loss_pct")
    plan["bias_confidence"] = "not calibrated"
    plan["bias_reasoning"] = {"freeform": "narrative only"}
    plan["risk_report"] = ["unstructured", "advice"]

    result = agent_gateway.publish_paper_plan("paper_test", plan, "metadata-20260723")

    assert result["published"] is True
    stored = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert stored["bias_confidence"] == 0
    assert "narrative only" in stored["bias_reasoning"]
    assert stored["risk_report"] == {"unstructured": ["unstructured", "advice"]}


def test_publish_is_paper_only(monkeypatch, tmp_path):
    patch_run(monkeypatch, tmp_path, mode="live")

    with pytest.raises(agent_gateway.GatewayError, match="paper runs"):
        agent_gateway.publish_paper_plan("live_test", valid_plan(), "safe-key-123")


def test_publish_is_idempotent_and_checks_expected_version(monkeypatch, tmp_path):
    patch_run(monkeypatch, tmp_path)
    (tmp_path / "plan.json").write_text(
        json.dumps({"updated": "v1", "plan_date": "2026-07-22"}),
        encoding="utf-8",
    )

    first = agent_gateway.publish_paper_plan(
        "paper_test", valid_plan(), "premarket-20260723", expected_updated="v1"
    )
    replay = agent_gateway.publish_paper_plan(
        "paper_test", valid_plan(), "premarket-20260723", expected_updated="stale"
    )

    assert first["published"] is True
    assert first["replayed"] is False
    assert replay["replayed"] is True
    stored = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert stored["updated_by"] == "codex-skill"


def test_publish_rejects_stale_expected_version(monkeypatch, tmp_path):
    patch_run(monkeypatch, tmp_path)
    (tmp_path / "plan.json").write_text(json.dumps({"updated": "v2"}), encoding="utf-8")

    with pytest.raises(agent_gateway.GatewayError, match="changed since"):
        agent_gateway.publish_paper_plan(
            "paper_test", valid_plan(), "premarket-20260724", expected_updated="v1"
        )


def test_plan_manager_refreshes_atomically_published_plan(tmp_path):
    manager = PlanManager(str(tmp_path))
    plan = valid_plan()
    plan["updated_by"] = "codex-skill"
    external = tmp_path / "plan.json.tmp"
    external.write_text(json.dumps(plan), encoding="utf-8")
    external.replace(tmp_path / "plan.json")

    assert manager.refresh_external() is True
    assert manager.load()["updated_by"] == "codex-skill"


def test_plan_manager_retries_transient_windows_replace_denial(tmp_path, monkeypatch):
    manager = PlanManager(str(tmp_path))
    real_replace = plan_module.os.replace
    calls = []

    def flaky_replace(source, target):
        calls.append((source, target))
        if len(calls) == 1:
            raise PermissionError(13, "transient sharing violation", target)
        return real_replace(source, target)

    monkeypatch.setattr(plan_module.os, "replace", flaky_replace)

    manager.save("test")

    assert len(calls) == 2
    assert manager.load()["updated_by"] == "test"
