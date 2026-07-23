from __future__ import annotations

import json

import pytest

from openalphastack.engine.ledger import Ledger
from openalphastack.engine.run_store import PlanRevisionConflict, RunStore
from openalphastack.engine.state import EngineState


def test_commit_trade_persists_state_and_ledger_together(tmp_path):
    store = RunStore(tmp_path)
    store.save_state({"cash": 100_000, "holdings": {}})

    revision, seq = store.commit_trade(
        {"cash": 90_000, "holdings": {"600519": {"shares": 100}}},
        {"decision": "buy", "symbol": "600519"},
    )

    state, loaded_revision = store.load_state()
    ledger = store.read_ledger()
    assert (revision, loaded_revision, seq) == (2, 2, 1)
    assert state["cash"] == 90_000
    assert ledger == [{"decision": "buy", "symbol": "600519", "seq": 1}]


def test_commit_trade_rolls_back_both_records_when_serialization_fails(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    store.save_state({"cash": 100_000, "holdings": {}})
    original_dump = store._dump
    calls = 0

    def fail_second_dump(payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("injected ledger serialization failure")
        return original_dump(payload)

    monkeypatch.setattr(store, "_dump", fail_second_dump)

    with pytest.raises(ValueError, match="injected ledger serialization failure"):
        store.commit_trade(
            {"cash": 90_000, "holdings": {"600519": {"shares": 100}}},
            {"decision": "buy", "symbol": "600519"},
        )

    assert store.load_state() == ({"cash": 100_000, "holdings": {}}, 1)
    assert store.read_ledger() == []


def test_restart_reads_canonical_sqlite_state_and_ledger(tmp_path):
    first = RunStore(tmp_path)
    first.commit_trade({"cash": 95_000}, {"decision": "buy", "symbol": "000001"})

    restarted = RunStore(tmp_path)

    assert restarted.load_state() == ({"cash": 95_000}, 1)
    assert restarted.read_ledger()[0]["symbol"] == "000001"


def test_snapshot_reports_consistent_revisions_and_ledger_tail(tmp_path):
    store = RunStore(tmp_path)
    store.save_plan({"plan_date": "2026-07-23"})
    store.commit_trade({"cash": 98_000}, {"decision": "buy", "symbol": "600000"})

    snapshot = store.read_snapshot()

    assert snapshot["state_revision"] == 1
    assert snapshot["plan_revision"] == 1
    assert snapshot["state"]["cash"] == 98_000
    assert snapshot["plan"]["plan_date"] == "2026-07-23"
    assert snapshot["ledger_tail"][0]["seq"] == 1


def test_legacy_json_and_jsonl_are_imported_once(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps({"initial_capital": 100_000, "cash": 88_000, "holdings": {}}),
        encoding="utf-8",
    )
    (tmp_path / "ledger.jsonl").write_text(
        json.dumps({"seq": 7, "decision": "buy", "symbol": "600036"}) + "\n",
        encoding="utf-8",
    )

    state = EngineState(str(tmp_path))
    ledger = Ledger(str(tmp_path))
    Ledger(str(tmp_path))

    assert state.cash == 88_000
    assert ledger.read_all() == [{"decision": "buy", "symbol": "600036", "seq": 7}]


def test_malformed_projections_do_not_override_valid_sqlite(tmp_path):
    store = RunStore(tmp_path)
    store.save_state({"initial_capital": 100_000, "cash": 91_000, "holdings": {}})
    store.append_ledger({"decision": "buy", "symbol": "000002"})
    (tmp_path / "state.json").write_text("{not-json", encoding="utf-8")
    (tmp_path / "ledger.jsonl").write_text("{not-json\n", encoding="utf-8")

    state = EngineState(str(tmp_path))
    ledger = Ledger(str(tmp_path))

    assert state.cash == 91_000
    assert ledger.read_all()[0]["symbol"] == "000002"


def test_projection_failure_does_not_turn_committed_trade_into_failure(tmp_path, monkeypatch):
    state = EngineState(str(tmp_path))
    ledger = Ledger(str(tmp_path))
    state.add_holding("600519", 100, 10.0, "test", persist=False)
    monkeypatch.setattr(state, "_export_json", lambda: (_ for _ in ()).throw(OSError("disk projection failed")))
    monkeypatch.setattr(ledger, "export_jsonl", lambda: (_ for _ in ()).throw(OSError("disk projection failed")))

    seq = state.commit_trade(ledger, {"decision": "buy", "symbol": "600519"})

    assert seq == 1
    assert RunStore(tmp_path).load_state()[0]["holdings"]["600519"]["shares"] == 100
    assert RunStore(tmp_path).read_ledger()[0]["symbol"] == "600519"


def test_plan_publication_and_idempotency_record_commit_together(tmp_path):
    store = RunStore(tmp_path)
    store.save_plan({"updated": "v1", "plan_date": "2026-07-23"})
    mutation = {"idempotency_key": "publish-key-001", "operation": "publish_paper_plan"}

    revision, replayed, committed = store.publish_plan(
        {"updated": "v2", "plan_date": "2026-07-23"},
        mutation,
        expected_updated="v1",
    )
    replay_revision, replayed_again, replay_mutation = store.publish_plan(
        {"updated": "v3", "plan_date": "2026-07-24"},
        mutation,
        expected_updated="stale-value-is-ignored-for-replay",
    )

    assert revision == 2
    assert replayed is False
    assert committed == mutation
    assert replay_revision == 0
    assert replayed_again is True
    assert replay_mutation == mutation
    assert store.load_plan() == ({"updated": "v2", "plan_date": "2026-07-23"}, 2)


def test_stale_plan_publication_changes_neither_plan_nor_idempotency(tmp_path):
    store = RunStore(tmp_path)
    store.save_plan({"updated": "current"})

    with pytest.raises(PlanRevisionConflict):
        store.publish_plan(
            {"updated": "next"},
            {"idempotency_key": "publish-key-002"},
            expected_updated="stale",
        )

    assert store.load_plan() == ({"updated": "current"}, 1)
    revision, replayed, _mutation = store.publish_plan(
        {"updated": "next"},
        {"idempotency_key": "publish-key-002"},
        expected_updated="current",
    )
    assert (revision, replayed) == (2, False)
