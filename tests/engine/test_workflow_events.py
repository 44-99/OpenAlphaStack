import json

from openalphastack.engine.workflow_events import (
    WorkflowEventStore,
    WORKFLOW_STAGES,
    default_workflow_edges,
    workflow_stage_id,
)


def test_default_workflow_is_three_stages():
    assert list(WORKFLOW_STAGES) == ["research", "execution", "evaluation"]
    assert default_workflow_edges() == [
        {
            "from": "research",
            "to": "execution",
            "kind": "data",
            "label": "Published paper plan",
            "refs": ["market.snapshot", "account.state", "plan.published"],
            "required": True,
        },
        {
            "from": "execution",
            "to": "evaluation",
            "kind": "data",
            "label": "State and ledger",
            "refs": ["account.state", "account.ledger"],
            "required": False,
        },
    ]


def test_legacy_nodes_map_to_stable_stages():
    assert workflow_stage_id("market_snapshot", "premarket") == "research"
    assert workflow_stage_id("risk_validation", "premarket") == "research"
    assert workflow_stage_id("fastlane_tick", "intraday") == "execution"
    assert workflow_stage_id("trade_attribution", "postclose") == "evaluation"


def test_record_event_persists_stage_and_artifacts(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")

    event = store.record_node_finish(
        phase="premarket",
        node_id="agent_research",
        node_name="Agent research",
        summary="plan published",
        input_payload={"market": "snapshot"},
        output_payload={"plan": "published"},
    )

    row = json.loads((tmp_path / "workflow_events.jsonl").read_text(encoding="utf-8"))
    assert row["stage_id"] == "research"
    assert event["stage_id"] == "research"
    assert (tmp_path / row["artifact_dir"] / "input.json").exists()
    assert (tmp_path / row["artifact_dir"] / "output.json").exists()


def test_read_events_adds_stage_to_historical_rows(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    (tmp_path / "workflow_events.jsonl").write_text(
        json.dumps({
            "event_id": "legacy",
            "run_id": "paper_test",
            "phase": "intraday",
            "node_id": "intraday_event_stream",
            "node_name": "legacy stream",
            "status": "success",
        }),
        encoding="utf-8",
    )

    assert store.read_events()[0]["stage_id"] == "execution"


def test_graph_aggregates_latest_detailed_event_by_stage(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    store.record_node_finish(
        phase="premarket",
        node_id="market_snapshot",
        node_name="market snapshot",
        summary="snapshot ready",
    )
    store.record_node_start(
        phase="premarket",
        node_id="agent_research",
        node_name="agent research",
        summary="publishing plan",
        input_refs=["market.snapshot"],
    )

    graph = store.build_graph()
    research = next(node for node in graph["nodes"] if node["id"] == "research")

    assert [node["id"] for node in graph["nodes"]] == ["research", "execution", "evaluation"]
    assert research["status"] == "running"
    assert research["summary"] == "publishing plan"
    assert research["input_refs"] == ["market.snapshot"]


def test_read_events_reports_corrupt_json_and_filters_empty_ticks(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")
    (tmp_path / "workflow_events.jsonl").write_text(
        json.dumps({
            "event_id": "empty_tick",
            "phase": "intraday",
            "node_id": "fastlane_tick",
            "summary": "tick 完成，监控 1 只，事件 0 条",
        }, ensure_ascii=False) + "\n{bad json\n",
        encoding="utf-8",
    )

    events = store.read_events()
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert "workflow_events.jsonl" in events[0]["summary"]
