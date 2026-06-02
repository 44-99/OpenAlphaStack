import pandas as pd

from alphaclaude.app import dashboard as app_dashboard


def _isolate_kline_cache(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    kline_dir = data_dir / "cache" / "kline"
    legacy_minute_dir = data_dir / "cache" / "minute"
    monkeypatch.setattr(app_dashboard, "DATA_DIR", data_dir)
    monkeypatch.setattr(app_dashboard, "KLINE_CACHE_DIR", str(kline_dir))
    monkeypatch.setattr(app_dashboard, "LEGACY_MINUTE_CACHE_DIR", str(legacy_minute_dir))
    monkeypatch.setattr(app_dashboard, "MINUTE_CACHE_DIR", str(legacy_minute_dir))
    return data_dir, kline_dir, legacy_minute_dir


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
