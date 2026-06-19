from __future__ import annotations

from alphaclaude.engine.agent_event import (
    read_agent_events,
    record_agent_event,
    validate_agent_events,
)


def test_record_and_read_agent_events(tmp_path):
    run_dir = tmp_path / "agent_runs" / "premarket_plan"

    event = record_agent_event(
        run_dir,
        task_id="market_intel",
        parent_task_id="premarket_plan",
        role="市场情报",
        status="running",
        summary="开始拉取市场信息",
        input_ref="tasks/market_intel/input.md",
    )
    record_agent_event(
        run_dir,
        task_id="market_intel",
        parent_task_id="premarket_plan",
        role="市场情报",
        status="success",
        summary="市场情报完成",
        output_ref="tasks/market_intel/output.md",
        result_ref="tasks/market_intel/result.json",
    )

    events = read_agent_events(run_dir)

    assert event["event_id"].startswith("agent_evt_")
    assert [item["status"] for item in events] == ["running", "success"]
    assert events[0]["task_id"] == "market_intel"
    assert events[0]["input_ref"] == "tasks/market_intel/input.md"
    assert events[1]["result_ref"] == "tasks/market_intel/result.json"


def test_read_agent_events_reports_corrupt_json(tmp_path):
    run_dir = tmp_path / "agent_runs" / "premarket_plan"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text('{"task_id":"ok"}\n{bad json\n', encoding="utf-8")

    events = read_agent_events(run_dir)

    assert events[0]["task_id"] == "ok"
    assert events[1]["status"] == "error"
    assert events[1]["task_id"] == "events.jsonl"
    assert "第 2 行损坏" in events[1]["summary"]


def test_validate_agent_events_detects_missing_audit_trail(tmp_path):
    result = validate_agent_events(tmp_path / "agent_runs" / "premarket_plan")

    assert result["ok"] is False
    assert "events.jsonl missing" in result["warnings"]


def test_validate_agent_events_detects_unclosed_and_missing_artifacts(tmp_path):
    run_dir = tmp_path / "agent_runs" / "premarket_plan"
    task_dir = run_dir / "tasks" / "candidate_discovery"
    task_dir.mkdir(parents=True)
    (task_dir / "input.md").write_text("input", encoding="utf-8")
    record_agent_event(
        run_dir,
        task_id="candidate_discovery",
        parent_task_id="premarket_plan",
        role="候选发现",
        status="running",
        input_ref="tasks/candidate_discovery/input.md",
    )
    record_agent_event(
        run_dir,
        task_id="holding_review",
        parent_task_id="premarket_plan",
        role="持仓复盘",
        status="success",
        output_ref="tasks/holding_review/output.md",
        result_ref="tasks/holding_review/result.json",
    )

    result = validate_agent_events(run_dir)

    assert result["ok"] is False
    assert "task candidate_discovery has no terminal event" in result["warnings"]
    assert "missing artifact: tasks/holding_review/output.md" in result["warnings"]
    assert "missing artifact: tasks/holding_review/result.json" in result["warnings"]
    assert result["tasks"]["candidate_discovery"]["status"] == "running"
    assert result["tasks"]["holding_review"]["status"] == "success"


def test_validate_agent_events_rejects_path_traversal_refs(tmp_path):
    run_dir = tmp_path / "agent_runs" / "premarket_plan"
    record_agent_event(
        run_dir,
        task_id="unsafe",
        status="success",
        output_ref="../outside.md",
    )

    result = validate_agent_events(run_dir)

    assert result["ok"] is False
    assert "unsafe artifact ref: ../outside.md" in result["warnings"]
