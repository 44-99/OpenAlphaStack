import asyncio
import base64
import json

import pandas as pd
from fastapi.responses import JSONResponse

from alphaclaude.app import dashboard as app_dashboard
from alphaclaude.engine import run_registry


def _isolate_kline_cache(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    kline_dir = data_dir / "cache" / "kline"
    legacy_minute_dir = data_dir / "cache" / "minute"
    monkeypatch.setattr(app_dashboard, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_dashboard, "KLINE_CACHE_DIR", str(kline_dir))
    monkeypatch.setattr(app_dashboard, "LEGACY_MINUTE_CACHE_DIR", str(legacy_minute_dir))
    monkeypatch.setattr(app_dashboard, "MINUTE_CACHE_DIR", str(legacy_minute_dir))
    return data_dir, kline_dir, legacy_minute_dir


def test_agent_terminal_startup_args_hides_utf8_setup(monkeypatch):
    monkeypatch.setattr(app_dashboard, "_agent_terminal_command", lambda _provider: "claude")

    args = app_dashboard._agent_terminal_startup_args("claude")
    encoded = args.rsplit(" ", 1)[-1]
    script = base64.b64decode(encoded).decode("utf-16-le")

    assert "-EncodedCommand" in args
    assert "[Console]::OutputEncoding" in script
    assert "chcp 65001 | Out-Null" in script
    assert script.endswith("claude")


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

    result = asyncio.run(app_dashboard.api_workflow_graph("paper_2026-06-04T09-30-00"))

    assert result["run_id"] == "paper_2026-06-04T09-30-00"
    assert any(node["id"] == "risk_validation" for node in result["nodes"])
    assert result["edges"]


def test_workflow_artifact_rejects_path_traversal(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_workflow_artifact("paper_2026-06-04T09-30-00", "..", "secret.txt"))

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
