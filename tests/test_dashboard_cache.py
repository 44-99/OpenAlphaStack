import asyncio
import json

import pandas as pd
from fastapi.responses import JSONResponse

from openalphastack.app import dashboard as app_dashboard
from openalphastack.engine import run_registry


def _isolate_kline_cache(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    kline_dir = data_dir / "cache" / "kline"
    legacy_minute_dir = data_dir / "cache" / "minute"
    monkeypatch.setattr(app_dashboard, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_dashboard, "KLINE_CACHE_DIR", str(kline_dir))
    monkeypatch.setattr(app_dashboard, "LEGACY_MINUTE_CACHE_DIR", str(legacy_minute_dir))
    monkeypatch.setattr(app_dashboard, "MINUTE_CACHE_DIR", str(legacy_minute_dir))
    return data_dir, kline_dir, legacy_minute_dir


def test_workflow_graph_model_serializes_edge_from_alias():
    graph = {
        "run_id": "paper_test",
        "nodes": [
            {"id": "market_snapshot", "name": "市场快照", "enabled": True, "locked": False, "status": "success"},
            {"id": "agent_research", "name": "自主 Agent 研判", "enabled": True, "locked": False, "status": "idle"},
        ],
        "edges": [
            {
                "from": "market_snapshot",
                "to": "agent_research",
                "kind": "data",
                "label": "Agent 任务上下文",
                "refs": ["artifact.market.snapshot", "account.state", "rule.skills"],
                "required": True,
            }
        ],
    }

    payload = app_dashboard.WorkflowGraphModel.model_validate(graph).model_dump(by_alias=True)

    assert payload["edges"][0]["from"] == "market_snapshot"
    assert "from_" not in payload["edges"][0]


def test_kline_cache_stats_counts_new_and_legacy_files(tmp_path, monkeypatch):
    _, kline_dir, legacy_minute_dir = _isolate_kline_cache(tmp_path, monkeypatch)
    (kline_dir / "day").mkdir(parents=True)
    legacy_minute_dir.mkdir(parents=True)
    (kline_dir / "day" / "000001.json").write_bytes(b"abc")
    (legacy_minute_dir / "000001_1m.parquet").write_bytes(b"defg")
    (legacy_minute_dir / "nested").mkdir()

    stats = app_dashboard._kline_cache_stats()["kline_cache"]

    assert stats["files"] == 2
    assert stats["bytes"] == 7
    assert stats["mb"] == round(7 / 1024 / 1024, 3)
    assert stats["layers"]["kline"]["files"] == 1
    assert stats["layers"]["legacy_minute"]["files"] == 1
    assert stats["updated_at"]


def test_clear_kline_cache_deletes_only_kline_cache_files(tmp_path, monkeypatch):
    data_dir, kline_dir, legacy_minute_dir = _isolate_kline_cache(tmp_path, monkeypatch)
    (kline_dir / "day").mkdir(parents=True)
    legacy_minute_dir.mkdir(parents=True)
    unrelated_dir = data_dir / "cache" / "quote"
    unrelated_dir.mkdir(parents=True)
    keep_dir = kline_dir / "nested"
    keep_dir.mkdir()
    (kline_dir / "day" / "000001.json").write_bytes(b"abc")
    (legacy_minute_dir / "000001_1m.parquet").write_bytes(b"defg")
    (keep_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (unrelated_dir / "quote_000001.json").write_text("keep", encoding="utf-8")

    result = app_dashboard._clear_kline_cache()

    assert result["removed_files"] == 3
    assert result["removed_bytes"] == 11
    assert result["kline_cache"]["files"] == 0
    assert keep_dir.exists()
    assert (unrelated_dir / "quote_000001.json").exists()


def test_clear_kline_cache_rejects_path_outside_cache(tmp_path, monkeypatch):
    data_dir, _, legacy_minute_dir = _isolate_kline_cache(tmp_path, monkeypatch)
    unsafe_dir = tmp_path / "outside"
    unsafe_dir.mkdir()

    monkeypatch.setattr(app_dashboard, "KLINE_CACHE_DIR", str(unsafe_dir))
    monkeypatch.setattr(app_dashboard, "LEGACY_MINUTE_CACHE_DIR", str(legacy_minute_dir))
    monkeypatch.setattr(app_dashboard, "DATA_DIR", data_dir)

    try:
        app_dashboard._clear_kline_cache()
    except RuntimeError as exc:
        assert "Refusing unsafe cache path" in str(exc)
    else:
        raise AssertionError("unsafe cache path was not rejected")


def test_minute_period_resamples_from_1m_cache(tmp_path, monkeypatch):
    _, kline_dir, _ = _isolate_kline_cache(tmp_path, monkeypatch)
    one_minute = pd.DataFrame({
        "time": pd.date_range("2026-06-02 09:30", periods=10, freq="min"),
        "open": [10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
        "high": [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
        "low": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
        "close": [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5],
        "volume": [100] * 10,
    })
    one_minute_path = kline_dir / "1m" / "000001.parquet"
    one_minute_path.parent.mkdir(parents=True)
    one_minute.to_parquet(one_minute_path, index=False)
    monkeypatch.setattr(app_dashboard, "_fetch_tencent_minute_df", lambda _code, _limit: pd.DataFrame())

    five_minute = app_dashboard._load_minute_kline_df("000001", "5m", 10)

    assert len(five_minute) == 2
    assert five_minute.iloc[0]["open"] == 10
    assert five_minute.iloc[0]["high"] == 15
    assert five_minute.iloc[0]["low"] == 9
    assert five_minute.iloc[0]["close"] == 14.5
    assert five_minute.iloc[0]["volume"] == 500
    assert (kline_dir / "5m" / "000001.parquet").exists()


def test_month_period_resamples_from_day_cache(tmp_path, monkeypatch):
    _, kline_dir, _ = _isolate_kline_cache(tmp_path, monkeypatch)
    day_df = pd.DataFrame({
        "time": pd.to_datetime(["2026-01-02", "2026-01-30", "2026-02-02", "2026-02-27"]),
        "open": [10, 11, 12, 13],
        "high": [12, 13, 14, 15],
        "low": [9, 10, 11, 12],
        "close": [11, 12, 13, 14],
        "volume": [100, 200, 300, 400],
    })
    day_path = kline_dir / "day" / "000001.json"
    app_dashboard._write_kline_json(str(day_path), day_df)
    monkeypatch.setattr(app_dashboard, "_fetch_tencent_day_df", lambda _code, _limit: pd.DataFrame())

    month = app_dashboard._load_month_kline_df("000001", 10)

    assert len(month) == 2
    assert month.iloc[0]["open"] == 10
    assert month.iloc[0]["high"] == 13
    assert month.iloc[0]["low"] == 9
    assert month.iloc[0]["close"] == 12
    assert month.iloc[0]["volume"] == 300
    assert (kline_dir / "month" / "000001.json").exists()


def test_stale_day_cache_fetches_and_merges_latest_trade_day(tmp_path, monkeypatch):
    _, kline_dir, _ = _isolate_kline_cache(tmp_path, monkeypatch)
    cached_times = pd.date_range("2026-04-06", periods=60, freq="D")
    cached = pd.DataFrame({
        "time": cached_times,
        "open": [10] * 60,
        "high": [12] * 60,
        "low": [9] * 60,
        "close": [11] * 60,
        "volume": [100] * 60,
    })
    fetched = pd.DataFrame({
        "time": pd.to_datetime(["2026-06-04", "2026-06-05"]),
        "open": [11.5, 12],
        "high": [13.5, 14],
        "low": [10.5, 11],
        "close": [12.5, 13],
        "volume": [250, 300],
    })
    app_dashboard._write_kline_json(str(kline_dir / "day" / "000001.json"), cached)
    monkeypatch.setattr(app_dashboard, "_kline_now", lambda: pd.Timestamp("2026-06-05 10:00").to_pydatetime())
    monkeypatch.setattr(app_dashboard, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(app_dashboard, "_fetch_tencent_day_df", lambda _code, _limit: fetched)

    day = app_dashboard._load_day_kline_df("000001", 260)

    assert day["time"].dt.strftime("%Y-%m-%d").tail(3).tolist() == ["2026-06-03", "2026-06-04", "2026-06-05"]
    assert day.iloc[-2]["close"] == 12.5
    saved = app_dashboard._read_kline_json(str(kline_dir / "day" / "000001.json"))
    assert saved.iloc[-1]["time"].strftime("%Y-%m-%d") == "2026-06-05"


def test_fresh_day_cache_does_not_fetch(tmp_path, monkeypatch):
    _, kline_dir, _ = _isolate_kline_cache(tmp_path, monkeypatch)
    cached_times = pd.date_range("2026-04-07", periods=60, freq="D")
    cached = pd.DataFrame({
        "time": cached_times,
        "open": [10] * 60,
        "high": [12] * 60,
        "low": [9] * 60,
        "close": [11] * 60,
        "volume": [100] * 60,
    })
    app_dashboard._write_kline_json(str(kline_dir / "day" / "000001.json"), cached)
    monkeypatch.setattr(app_dashboard, "_kline_now", lambda: pd.Timestamp("2026-06-05 10:00").to_pydatetime())
    monkeypatch.setattr(app_dashboard, "is_trading_day", lambda _day: True)

    def fail_fetch(_code, _limit):
        raise AssertionError("fresh cache should not fetch")

    monkeypatch.setattr(app_dashboard, "_fetch_tencent_day_df", fail_fetch)

    day = app_dashboard._load_day_kline_df("000001", 260)

    assert len(day) == 60
    assert day.iloc[-1]["time"].strftime("%Y-%m-%d") == "2026-06-05"


def test_stale_one_minute_cache_fetches_and_merges_latest_bars(tmp_path, monkeypatch):
    _, kline_dir, _ = _isolate_kline_cache(tmp_path, monkeypatch)
    cached_times = pd.date_range("2026-06-05 07:32", periods=120, freq="min")
    cached = pd.DataFrame({
        "time": cached_times,
        "open": [10] * 120,
        "high": [12] * 120,
        "low": [9] * 120,
        "close": [11] * 120,
        "volume": [100] * 120,
    })
    fetched = pd.DataFrame({
        "time": pd.to_datetime(["2026-06-05 09:31", "2026-06-05 09:32"]),
        "open": [11.5, 12],
        "high": [13.5, 14],
        "low": [10.5, 11],
        "close": [12.5, 13],
        "volume": [250, 300],
    })
    one_minute_path = kline_dir / "1m" / "000001.parquet"
    one_minute_path.parent.mkdir(parents=True)
    cached.to_parquet(one_minute_path, index=False)
    monkeypatch.setattr(app_dashboard, "_kline_now", lambda: pd.Timestamp("2026-06-05 09:33").to_pydatetime())
    monkeypatch.setattr(app_dashboard, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(app_dashboard, "_fetch_tencent_minute_df", lambda _code, _limit: fetched)

    minute = app_dashboard._load_1m_kline_df("000001", 260)

    assert minute["time"].dt.strftime("%Y-%m-%d %H:%M").tail(3).tolist() == [
        "2026-06-05 09:30",
        "2026-06-05 09:31",
        "2026-06-05 09:32",
    ]
    assert minute.iloc[-2]["close"] == 12.5


def test_aggregated_minute_period_regenerates_from_refreshed_one_minute_cache(tmp_path, monkeypatch):
    _, kline_dir, _ = _isolate_kline_cache(tmp_path, monkeypatch)
    old_five = pd.DataFrame({
        "time": pd.to_datetime(["2026-06-04 11:30"]),
        "open": [10],
        "high": [11],
        "low": [9],
        "close": [10.5],
        "volume": [100],
    })
    one_minute = pd.DataFrame({
        "time": pd.to_datetime(["2026-06-05 09:30", "2026-06-05 09:31", "2026-06-05 09:32", "2026-06-05 09:33", "2026-06-05 09:34"]),
        "open": [20, 21, 22, 23, 24],
        "high": [21, 22, 23, 24, 25],
        "low": [19, 20, 21, 22, 23],
        "close": [20.5, 21.5, 22.5, 23.5, 24.5],
        "volume": [100] * 5,
    })
    five_path = kline_dir / "5m" / "000001.parquet"
    five_path.parent.mkdir(parents=True)
    old_five.to_parquet(five_path, index=False)
    monkeypatch.setattr(app_dashboard, "_load_1m_kline_df", lambda _code, _limit: one_minute)

    five = app_dashboard._load_minute_kline_df("000001", "5m", 260)

    assert len(five) == 1
    assert five.iloc[0]["time"].strftime("%Y-%m-%d %H:%M") == "2026-06-05 09:30"
    assert five.iloc[0]["close"] == 24.5


def test_minute_cache_before_next_open_expects_previous_trading_close(monkeypatch):
    monkeypatch.setattr(app_dashboard, "is_trading_day", lambda _day: True)

    expected = app_dashboard._expected_latest_minute_time(
        pd.Timestamp("2026-06-10 01:09").to_pydatetime()
    )

    assert expected == pd.Timestamp("2026-06-09 15:00").to_pydatetime()


def test_engine_status_uses_run_registry_liveness(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-02T08-00-00"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "data_time": "2026-06-02 08:48:32",
            "engine_meta": {
                "mode": "paper",
                "process_id": 31560,
                "status": "running",
                "observation_mode": False,
                "observation_reason": "",
            },
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))
    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda _pid: False)

    result = asyncio.run(app_dashboard.api_engine_status())

    assert result["run_id"] == "paper_2026-06-02T08-00-00"
    assert result["status"] == "stopped"
    assert result["is_alive"] is False
    assert result["process_id"] == 31560


def test_runs_api_includes_agent_runs(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "agent_2026-06-20_postclose_review"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "data_time": "2026-06-20 15:30:00",
            "engine_meta": {
                "mode": "agent",
                "agent_task_id": "postclose_review",
                "process_id": 0,
                "status": "completed",
                "started_at": "2026-06-20T15:30:00",
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))
    monkeypatch.setattr(run_registry, "_output_base", lambda: output)
    monkeypatch.setattr(run_registry, "_is_pid_alive", lambda _pid: False)

    result = asyncio.run(app_dashboard.api_runs())

    assert result["selected_run_id"] == "agent_2026-06-20_postclose_review"
    assert result["runs"][0]["mode"] == "agent"


def test_workflow_events_api_reads_active_run(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{}", encoding="utf-8")
    (run_dir / "workflow_events.jsonl").write_text(
        (
            '{"event_id":"wf_1","run_id":"paper_2026-06-04T09-30-00",'
            '"phase":"premarket","node_id":"risk_validation","node_name":"风控校验",'
            '"status":"success","started_at":"","ended_at":"","duration_ms":0,'
            '"input_refs":[],"output_refs":[],"summary":"passed","error":"","artifact_dir":""}\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_workflow_events("paper_2026-06-04T09-30-00"))

    assert result["run_id"] == "paper_2026-06-04T09-30-00"
    assert result["events"][0]["node_id"] == "risk_validation"


def test_workflow_graph_api_returns_default_nodes(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))
    monkeypatch.setattr(app_dashboard, "is_trading_day", lambda _day: True)

    result = asyncio.run(app_dashboard.api_workflow_graph("paper_2026-06-04T09-30-00"))

    assert result["run_id"] == "paper_2026-06-04T09-30-00"
    assert any(node["id"] == "risk_validation" for node in result["nodes"])
    assert result["edges"]
    assert result["is_alive"] is False
    assert result["run_status"] in {"stopped", "unknown"}
    assert result["market_status"] in {"trading", "stale"}
    assert result["calendar_date"]
    assert result["display_date"]


def test_workflow_graph_api_marks_closed_market_day(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({"data_time": "2026-06-06 12:00:00"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (run_dir / "workflow_events.jsonl").write_text(
        json.dumps({
            "event_id": "wf_daily",
            "run_id": "paper_2026-06-04T09-30-00",
            "phase": "postclose",
            "node_id": "daily_report",
            "node_name": "盘后日报",
            "status": "success",
            "started_at": "2026-06-05T15:10:00",
            "ended_at": "2026-06-05T15:12:00",
            "summary": "盘后完成",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))
    monkeypatch.setattr(app_dashboard, "is_trading_day", lambda _day: False)
    monkeypatch.setattr(app_dashboard, "non_trading_reason", lambda _day: "周六休市")

    result = asyncio.run(app_dashboard.api_workflow_graph("paper_2026-06-04T09-30-00"))

    assert result["market_status"] == "closed"
    assert result["is_trading_day"] is False
    assert result["display_date"] == "2026-06-05"
    assert "今日休市" in result["market_message"]


def test_dashboard_demo_mode_returns_onboarding_data(tmp_path, monkeypatch):
    output = tmp_path / "empty_output"
    output.mkdir()
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    state = asyncio.run(app_dashboard.api_state())
    plan = asyncio.run(app_dashboard.api_plan())
    ledger = asyncio.run(app_dashboard.api_ledger(limit=10, code="300913"))
    graph = asyncio.run(app_dashboard.api_workflow_graph("active"))
    kline = asyncio.run(app_dashboard.api_kline("300913", period="1m", limit=90))
    annotations = asyncio.run(app_dashboard.api_kline_annotations("300913", period="1m"))

    assert state["run_id"] == app_dashboard.DEMO_RUN_ID
    assert plan["buy_candidates"]
    assert ledger and ledger[0]["symbol"] == "300913"
    assert graph["run_id"] == app_dashboard.DEMO_RUN_ID
    assert kline["source"] == "1m_demo"
    assert annotations["annotations"]


def test_dashboard_live_run_controls_are_locked():
    class FakeRequest:
        async def json(self):
            return {"mode": "live"}

    start = asyncio.run(app_dashboard.api_run_start(FakeRequest()))
    resume = asyncio.run(app_dashboard.api_run_resume("live_2026-06-05T09-30-00"))
    stop = asyncio.run(app_dashboard.api_run_stop("live_2026-06-05T09-30-00"))

    assert isinstance(start, JSONResponse)
    assert isinstance(resume, JSONResponse)
    assert isinstance(stop, JSONResponse)
    assert start.status_code == 423
    assert resume.status_code == 423
    assert stop.status_code == 423


def test_kline_annotations_api_filters_and_normalizes_active_run(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    (run_dir / "kline_annotations.json").write_text(
        json.dumps({
            "annotations": [
                {
                    "code": "300913",
                    "period": "day",
                    "kind": "level",
                    "label": "关键支撑",
                    "price": "10.5",
                    "source": {"skill": "pivot", "confidence": "72"},
                },
                {"code": "300913", "period": "week", "kind": "level", "label": "周线压力", "price": 12},
                {"code": "000001", "period": "day", "kind": "level", "label": "其他股票", "price": 9},
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_kline_annotations("300913", "day"))

    assert result["code"] == "300913"
    assert len(result["annotations"]) == 1
    assert result["annotations"][0]["label"] == "关键支撑"
    assert result["annotations"][0]["price"] == 10.5
    assert result["annotations"][0]["source"]["confidence"] == 72


def test_kline_annotations_api_generates_when_missing(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))
    monkeypatch.setattr(app_dashboard, "_generate_kline_annotations_from_tools", lambda code, period: [
        {"id": "gen_1", "code": code, "period": "all", "kind": "level", "label": "自动支撑", "tone": "up", "price": 10.5}
    ])

    result = asyncio.run(app_dashboard.api_kline_annotations("300913", "day"))

    assert result["annotations"][0]["label"] == "自动支撑"
    assert (run_dir / "kline_annotations" / "300913.json").exists()


def test_workflow_node_rerun_queues_only_safe_nodes(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    accepted = asyncio.run(app_dashboard.api_workflow_node_rerun("paper_2026-06-04T09-30-00", "market_snapshot"))
    blocked = asyncio.run(app_dashboard.api_workflow_node_rerun("paper_2026-06-04T09-30-00", "intraday_event_stream"))

    assert accepted.status_code == 202
    assert (run_dir / app_dashboard.RERUN_REQUESTS_FILE).exists()
    assert isinstance(blocked, JSONResponse)
    assert blocked.status_code == 409


def test_workflow_artifact_rejects_path_traversal(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_workflow_artifact("paper_2026-06-04T09-30-00", "..", "secret.txt"))

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


def test_agent_run_timeline_reads_dynamic_subtasks(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    agent_dir = run_dir / "agent_runs" / "premarket_plan"
    task_dir = agent_dir / "tasks" / "market_intel"
    task_dir.mkdir(parents=True)
    (task_dir / "input.md").write_text("input", encoding="utf-8")
    (task_dir / "output.md").write_text("output", encoding="utf-8")
    (task_dir / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (agent_dir / "events.jsonl").write_text(
        "\n".join([
            json.dumps({
                "event_id": "agent_evt_1",
                "task_id": "market_intel",
                "parent_task_id": "premarket_plan",
                "role": "市场情报",
                "status": "running",
                "summary": "开始",
                "input_ref": "tasks/market_intel/input.md",
            }, ensure_ascii=False),
            json.dumps({
                "event_id": "agent_evt_2",
                "task_id": "market_intel",
                "parent_task_id": "premarket_plan",
                "role": "市场情报",
                "status": "success",
                "summary": "完成",
                "output_ref": "tasks/market_intel/output.md",
                "result_ref": "tasks/market_intel/result.json",
            }, ensure_ascii=False),
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_agent_run_timeline("paper_2026-06-04T09-30-00", "premarket_plan"))

    assert result["run_id"] == "paper_2026-06-04T09-30-00"
    assert result["task_id"] == "premarket_plan"
    assert result["warnings"] == []
    assert len(result["events"]) == 2
    assert result["tasks"]["market_intel"]["status"] == "success"
    assert result["tasks"]["market_intel"]["input_ref"] == "tasks/market_intel/input.md"


def test_agent_run_timeline_reads_postclose_review(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "agent_2026-06-20_postclose_review"
    agent_dir = run_dir / "agent_runs" / "postclose_review"
    task_dir = agent_dir / "tasks" / "trade_review"
    task_dir.mkdir(parents=True)
    (task_dir / "input.md").write_text("ledger", encoding="utf-8")
    (task_dir / "output.md").write_text("review", encoding="utf-8")
    (task_dir / "result.json").write_text('{"ok": true}', encoding="utf-8")
    (agent_dir / "events.jsonl").write_text(
        json.dumps({
            "event_id": "agent_evt_postclose",
            "task_id": "trade_review",
            "parent_task_id": "postclose_review",
            "role": "盘后归因",
            "status": "success",
            "started_at": "2026-06-20T15:30:00",
            "ended_at": "2026-06-20T15:31:00",
            "summary": "复盘完成",
            "input_ref": "tasks/trade_review/input.md",
            "output_ref": "tasks/trade_review/output.md",
            "result_ref": "tasks/trade_review/result.json",
            "error": "",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_agent_run_timeline("agent_2026-06-20_postclose_review", "postclose_review"))

    assert result["run_id"] == "agent_2026-06-20_postclose_review"
    assert result["task_id"] == "postclose_review"
    assert result["tasks"]["trade_review"]["role"] == "盘后归因"
    assert result["warnings"] == []


def test_agent_run_timeline_rejects_unsafe_task_id(tmp_path, monkeypatch):
    output = tmp_path / "output"
    (output / "paper_2026-06-04T09-30-00").mkdir(parents=True)
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_agent_run_timeline("paper_2026-06-04T09-30-00", ".."))

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


def test_agent_run_artifact_reads_safe_ref(tmp_path, monkeypatch):
    output = tmp_path / "output"
    artifact = output / "paper_2026-06-04T09-30-00" / "agent_runs" / "premarket_plan" / "tasks" / "market_intel" / "output.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("market output", encoding="utf-8")
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(
        app_dashboard.api_agent_run_artifact(
            "paper_2026-06-04T09-30-00",
            "premarket_plan",
            "tasks/market_intel/output.md",
        )
    )

    assert result["content"] == "market output"
    assert result["artifact_ref"] == "tasks/market_intel/output.md"


def test_agent_run_artifact_rejects_path_traversal(tmp_path, monkeypatch):
    output = tmp_path / "output"
    (output / "paper_2026-06-04T09-30-00" / "agent_runs" / "premarket_plan").mkdir(parents=True)
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(
        app_dashboard.api_agent_run_artifact(
            "paper_2026-06-04T09-30-00",
            "premarket_plan",
            "../secret.txt",
        )
    )

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400


def test_ledger_filter_matches_symbol_or_code(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    (run_dir / "ledger.jsonl").write_text(
        "\n".join([
            json.dumps({"symbol": "300913", "price": 10}),
            json.dumps({"code": "300913", "price": 11}),
            json.dumps({"symbol": "000001", "price": 12}),
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_ledger(limit=10, code="300913"))

    assert [row["price"] for row in result] == [10, 11]
