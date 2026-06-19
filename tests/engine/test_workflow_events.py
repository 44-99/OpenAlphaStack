import json

from alphaclaude.engine.workflow_events import (
    WorkflowEventStore,
    default_workflow_config,
    default_workflow_edges,
)


def test_record_node_finish_writes_jsonl_and_artifacts(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")

    event = store.record_node_finish(
        phase="premarket",
        node_id="risk_validation",
        node_name="风控校验",
        started_at="2026-06-04T09:30:01",
        summary="3 candidates, 2 passed, 1 rejected",
        input_refs=["plan.buy_candidates"],
        output_refs=["plan.risk_report"],
        input_payload={"candidates": [1, 2, 3]},
        output_payload={"passed": 2},
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "workflow_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert len(rows) == 1
    assert rows[0]["event_id"] == event["event_id"]
    assert rows[0]["status"] == "success"
    assert rows[0]["phase"] == "premarket"
    assert rows[0]["node_id"] == "risk_validation"
    assert rows[0]["artifact_dir"] == f"workflow_artifacts/{event['event_id']}"
    assert (tmp_path / rows[0]["artifact_dir"] / "input.json").exists()
    assert (tmp_path / rows[0]["artifact_dir"] / "output.json").exists()


def test_default_config_locks_risk_and_ledger_nodes():
    config = default_workflow_config()

    assert config["version"] == 1
    assert config["nodes"]["risk_validation"]["locked"] is True
    assert config["nodes"]["intraday_event_stream"]["locked"] is True
    assert config["nodes"]["risk_validation"]["enabled"] is True


def test_default_edges_declare_explicit_data_contracts():
    edges = default_workflow_edges()
    by_pair = {(edge["from"], edge["to"]): edge for edge in edges}

    assert ("market_snapshot", "agent_research") in by_pair
    assert by_pair[("market_snapshot", "agent_research")] == {
        "from": "market_snapshot",
        "to": "agent_research",
        "kind": "data",
        "label": "Agent 任务上下文",
        "refs": ["artifact.market.snapshot", "account.state", "rule.skills"],
        "required": True,
    }
    assert by_pair[("agent_research", "risk_validation")]["refs"] == [
        "artifact.agent.plan_draft",
        "artifact.agent.research",
    ]
    assert all("kind" in edge and "label" in edge and "refs" in edge for edge in edges)


def test_read_events_returns_diagnostic_for_corrupt_jsonl(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    (tmp_path / "workflow_events.jsonl").write_text('{"ok": true}\n{bad json\n', encoding="utf-8")

    events = store.read_events()

    assert events[0]["ok"] is True
    assert events[1]["status"] == "error"
    assert events[1]["node_id"] == "workflow_events"
    assert "workflow_events.jsonl" in events[1]["summary"]


def test_read_events_filters_noisy_legacy_zero_ticks(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    (tmp_path / "workflow_events.jsonl").write_text(
        "\n".join([
            json.dumps({
                "event_id": "wf_zero",
                "run_id": "paper_test",
                "phase": "intraday",
                "node_id": "fastlane_tick",
                "node_name": "盘中快车道",
                "status": "success",
                "summary": "tick 完成，监控 1 只，事件 0 条",
            }, ensure_ascii=False),
            json.dumps({
                "event_id": "wf_signal",
                "run_id": "paper_test",
                "phase": "intraday",
                "node_id": "fastlane_tick",
                "node_name": "盘中快车道",
                "status": "success",
                "summary": "tick 完成，监控 1 只，事件 1 条",
            }, ensure_ascii=False),
        ]),
        encoding="utf-8",
    )

    events = store.read_events()

    assert [event["event_id"] for event in events] == ["wf_signal"]


def test_build_graph_uses_latest_event_status(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    store.record_node_finish(
        phase="premarket",
        node_id="risk_validation",
        node_name="风控校验",
        summary="passed",
    )

    graph = store.build_graph()
    node = next(item for item in graph["nodes"] if item["id"] == "risk_validation")

    assert graph["run_id"] == "paper_test"
    assert node["name"] == "风控校验"
    assert node["status"] == "success"
    assert node["summary"] == "passed"
    assert graph["edges"]


def test_record_node_start_marks_graph_node_running(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    event = store.record_node_start(
        phase="premarket",
        node_id="agent_research",
        node_name="自主 Agent 研判",
        summary="正在执行盘前 Agent 任务",
        input_refs=["artifact.market.snapshot", "account.state", "rule.skills"],
        input_payload={"market": "snapshot"},
    )

    graph = store.build_graph()
    node = next(item for item in graph["nodes"] if item["id"] == "agent_research")

    assert event["status"] == "running"
    assert event["ended_at"] == ""
    assert node["status"] == "running"
    assert node["input_refs"] == ["artifact.market.snapshot", "account.state", "rule.skills"]
    assert node["artifact_dir"] == f"workflow_artifacts/{event['event_id']}"


def test_config_update_records_audit_event(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    config = store.write_config({
        "nodes": {
            "agent_research": {"enabled": False, "params": {"timeout_sec": 120}},
            "risk_validation": {"enabled": False, "params": {"max_single_position_pct": 15}},
        },
    })
    event = store.record_config_update(summary="配置更新", config=config)
    events = store.read_events()

    assert config["nodes"]["agent_research"]["enabled"] is False
    assert config["nodes"]["agent_research"]["params"]["timeout_sec"] == 120
    assert config["nodes"]["risk_validation"]["enabled"] is True
    assert config["nodes"]["risk_validation"]["params"]["max_single_position_pct"] == 15
    assert config["updated_at"]
    assert event["node_id"] == "workflow_config"
    assert events[-1]["summary"] == "配置更新"
    assert (tmp_path / events[-1]["artifact_dir"] / "output.json").exists()


def test_read_config_migrates_obsolete_nodes(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    legacy_config = default_workflow_config()
    legacy_config["nodes"]["signal_scan"] = {"enabled": True, "locked": False, "params": {}}
    legacy_config["nodes"]["sub_agent_a"] = {"enabled": False, "locked": False, "params": {"lookback": 20}}
    legacy_config["nodes"]["merge_decision"] = {"enabled": True, "locked": False, "params": {}}
    legacy_config["nodes"].pop("intraday_event_stream")
    (tmp_path / "workflow_config.json").write_text(
        json.dumps(legacy_config, ensure_ascii=False),
        encoding="utf-8",
    )

    migrated = store.read_config()

    assert "signal_scan" not in migrated["nodes"]
    assert "sub_agent_a" not in migrated["nodes"]
    assert "merge_decision" not in migrated["nodes"]
    assert "intraday_event_stream" in migrated["nodes"]
    assert "agent_research" in migrated["nodes"]
