import json

from alphaclaude.engine.workflow_events import (
    WorkflowEventStore,
    default_workflow_config,
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
    assert config["nodes"]["ledger_writer"]["locked"] is True
    assert config["nodes"]["risk_validation"]["enabled"] is True


def test_read_events_returns_diagnostic_for_corrupt_jsonl(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    (tmp_path / "workflow_events.jsonl").write_text('{"ok": true}\n{bad json\n', encoding="utf-8")

    events = store.read_events()

    assert events[0]["ok"] is True
    assert events[1]["status"] == "error"
    assert events[1]["node_id"] == "workflow_events"
    assert "workflow_events.jsonl" in events[1]["summary"]


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
        node_id="sub_agent_a",
        node_name="子代理A",
        summary="正在分析候选池",
        input_refs=["market.snapshot"],
        input_payload={"market": "snapshot"},
    )

    graph = store.build_graph()
    node = next(item for item in graph["nodes"] if item["id"] == "sub_agent_a")

    assert event["status"] == "running"
    assert event["ended_at"] == ""
    assert node["status"] == "running"
    assert node["input_refs"] == ["market.snapshot"]
    assert node["artifact_dir"] == f"workflow_artifacts/{event['event_id']}"


def test_config_update_records_audit_event(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    config = store.write_config({
        "nodes": {
            "sub_agent_a": {"enabled": False, "params": {"lookback": 20}},
            "risk_validation": {"enabled": False, "params": {"max_single_position_pct": 15}},
        },
    })
    event = store.record_config_update(summary="配置更新", config=config)
    events = store.read_events()

    assert config["nodes"]["sub_agent_a"]["enabled"] is False
    assert config["nodes"]["risk_validation"]["enabled"] is True
    assert config["nodes"]["risk_validation"]["params"]["max_single_position_pct"] == 15
    assert config["updated_at"]
    assert event["node_id"] == "workflow_config"
    assert events[-1]["summary"] == "配置更新"
    assert (tmp_path / events[-1]["artifact_dir"] / "output.json").exists()
