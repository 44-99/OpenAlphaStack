"""Shadow Account Phase B — comprehensive test coverage.

Tests all core functions in openalphastack.tools.shadow_account:
pair_trades, compute_diagnostics, _detect_patterns, format_for_prompt,
build_reflection_prompt, format_reflections_for_prompt, and IO functions.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from openalphastack.tools.shadow_account import (
    _detect_patterns,
    _entry_date,
    _find_base_date,
    _holding_days,
    build_reflection_prompt,
    compare_runs,
    compute_diagnostics,
    format_for_prompt,
    format_reflections_for_prompt,
    load_accumulated_patterns,
    load_latest_diagnostics,
    load_ledger,
    merge_patterns,
    pair_trades,
    prepare_reflection_prompt,
    save_diagnostics,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def temp_dir() -> Path:
    tmp_root = PROJECT_ROOT / "data" / "test_tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    path = tmp_root / f"shadow_test_{uuid.uuid4().hex}"
    path.mkdir(exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _make_open(code="600036", price=42.0, shares=1000, date="2025-03-14",
               strategy="breakout", decision="open_position", **kw):
    return {
        "decision": decision, "symbol": code, "price": price,
        "shares": shares, "date": date, "strategy": strategy, **kw,
    }


def _make_close(code="600036", price=43.0, shares=1000, pnl=1000.0,
                pnl_pct=2.38, date="2025-03-20", **kw):
    return {
        "decision": "close_position", "symbol": code, "price": price,
        "shares": shares, "pnl": pnl, "pnl_pct": pnl_pct,
        "date": date, **kw,
    }


def _make_bias(value="neutral", date="2025-03-14"):
    return {"decision": "overnight_bias", "value": value, "date": date}


@pytest.fixture
def single_pair():
    return [_make_open(), _make_close()]


@pytest.fixture
def all_winners():
    entries = []
    for i in range(5):
        entries.append(_make_open(code=f"6000{i:02d}", price=10.0 + i,
                                   shares=1000, date=f"2025-03-{14+i:02d}"))
        entries.append(_make_close(code=f"6000{i:02d}", price=12.0 + i,
                                    shares=1000, pnl=2000.0, pnl_pct=20.0,
                                    date=f"2025-03-{20+i:02d}"))
    return entries


@pytest.fixture
def all_losers():
    entries = []
    for i in range(5):
        entries.append(_make_open(code=f"6000{i:02d}", price=10.0 + i,
                                   shares=1000, date=f"2025-03-{14+i:02d}"))
        entries.append(_make_close(code=f"6000{i:02d}", price=8.0 + i,
                                    shares=1000, pnl=-2000.0, pnl_pct=-20.0,
                                    date=f"2025-03-{20+i:02d}"))
    return entries


@pytest.fixture
def mix_winners_losers():
    entries = []
    # 6 winners
    for i in range(6):
        entries.append(_make_open(code=f"6000{i:02d}", price=50.0,
                                   shares=1000, date="2025-03-14",
                                   strategy="breakout"))
        entries.append(_make_close(code=f"6000{i:02d}", price=55.0,
                                    shares=1000, pnl=5000.0, pnl_pct=10.0,
                                    date="2025-03-16"))
    # 4 losers (held longer — disposition effect)
    for i in range(6, 10):
        entries.append(_make_open(code=f"6000{i:02d}", price=50.0,
                                   shares=1000, date="2025-03-14",
                                   strategy="trend"))
        entries.append(_make_close(code=f"6000{i:02d}", price=45.0,
                                    shares=1000, pnl=-5000.0, pnl_pct=-10.0,
                                    date="2025-03-24",
                                    reasoning="止损 恐慌"))
    # 2 open positions
    entries.append(_make_open(code="600101", price=30.0, shares=500,
                               date="2025-03-25"))
    entries.append(_make_open(code="600102", price=80.0, shares=300,
                               date="2025-03-26"))
    # bias entries
    entries.insert(0, _make_bias("bullish", "2025-03-14"))
    return entries


@pytest.fixture
def empty_entries():
    return [_make_bias("neutral"), _make_bias("bearish")]


# ═══════════════════════════════════════════════════════════════════
# pair_trades tests
# ═══════════════════════════════════════════════════════════════════

class TestPairTrades:
    def test_normal(self, single_pair):
        paired, open_pos = pair_trades(single_pair)
        assert len(paired) == 1
        assert len(open_pos) == 0
        assert paired[0]["symbol"] == "600036"
        assert paired[0]["entry_price"] == 42.0
        assert paired[0]["exit_price"] == 43.0

    def test_fifo(self):
        entries = [
            _make_open(code="000001", price=10.0, shares=500, date="2025-03-14"),
            _make_open(code="000001", price=12.0, shares=500, date="2025-03-15"),
            _make_close(code="000001", price=13.0, shares=500, pnl=1500.0,
                        pnl_pct=30.0, date="2025-03-20"),
        ]
        paired, open_pos = pair_trades(entries)
        assert len(paired) == 1  # close matches first open
        assert paired[0]["entry_price"] == 10.0
        assert len(open_pos) == 1  # second open still open
        assert open_pos[0]["entry_price"] == 12.0

    def test_partial_close(self):
        entries = [
            _make_open(code="000001", price=10.0, shares=1000, date="2025-03-14"),
            _make_close(code="000001", price=13.0, shares=600, pnl=1800.0,
                        pnl_pct=30.0, date="2025-03-20"),
        ]
        paired, open_pos = pair_trades(entries)
        assert len(paired) == 1
        assert paired[0]["shares"] == 600
        assert len(open_pos) == 1
        assert open_pos[0]["remaining_shares"] == 400

    def test_multiple_closes(self):
        entries = [
            _make_open(code="000001", price=10.0, shares=900, date="2025-03-14"),
            _make_close(code="000001", price=12.0, shares=500, pnl=1000.0,
                        pnl_pct=20.0, date="2025-03-16"),
            _make_close(code="000001", price=14.0, shares=400, pnl=1600.0,
                        pnl_pct=40.0, date="2025-03-18"),
        ]
        paired, open_pos = pair_trades(entries)
        assert len(paired) == 2
        assert len(open_pos) == 0

    def test_empty(self):
        paired, open_pos = pair_trades([])
        assert paired == []
        assert open_pos == []

    def test_only_open(self):
        entries = [_make_open()]
        paired, open_pos = pair_trades(entries)
        assert len(paired) == 0
        assert len(open_pos) == 1

    def test_close_without_open(self):
        entries = [_make_close()]
        paired, open_pos = pair_trades(entries)
        assert len(paired) == 0
        assert len(open_pos) == 0

    def test_missing_fields(self):
        entries = [
            {"decision": "open_position", "symbol": "000001"},
            {"decision": "close_position", "symbol": "000001", "pnl": 0, "shares": 100},
        ]
        paired, open_pos = pair_trades(entries)
        assert len(paired) == 1
        assert paired[0]["entry_price"] == 0


# ═══════════════════════════════════════════════════════════════════
# compute_diagnostics tests
# ═══════════════════════════════════════════════════════════════════

class TestComputeDiagnostics:
    def test_normal(self, mix_winners_losers):
        paired, open_pos = pair_trades(mix_winners_losers)
        diag = compute_diagnostics(paired, open_pos, mix_winners_losers)
        assert diag["paired_trades_count"] == 10
        assert diag["open_positions_count"] == 2
        assert diag["summary"]["win_rate"] == 60.0
        assert diag["summary"]["total_pnl"] == 10000.0

    def test_all_winners(self, all_winners):
        paired, open_pos = pair_trades(all_winners)
        diag = compute_diagnostics(paired, open_pos, all_winners)
        assert diag["summary"]["win_rate"] == 100.0
        assert diag["summary"]["total_pnl"] > 0

    def test_all_losers(self, all_losers):
        paired, open_pos = pair_trades(all_losers)
        diag = compute_diagnostics(paired, open_pos, all_losers)
        assert diag["summary"]["win_rate"] == 0.0
        assert diag["summary"]["total_pnl"] < 0

    def test_empty_paired(self):
        diag = compute_diagnostics([], [], [])
        assert diag["paired_trades_count"] == 0
        assert diag["summary"].get("win_rate", 0) == 0

    def test_single_trade(self, single_pair):
        paired, open_pos = pair_trades(single_pair)
        diag = compute_diagnostics(paired, open_pos, single_pair)
        assert diag["summary"]["win_rate"] == 100.0

    def test_disposition_effect_triggered(self):
        entries = [
            _make_open(code="000001", price=10.0, shares=1000, date="2025-03-14"),
            _make_close(code="000001", price=11.0, shares=1000, pnl=1000.0,
                        pnl_pct=10.0, date="2025-03-15"),  # held 1 day
            _make_open(code="000002", price=20.0, shares=1000, date="2025-03-14"),
            _make_close(code="000002", price=18.0, shares=1000, pnl=-2000.0,
                        pnl_pct=-10.0, date="2025-03-24"),  # held 10 days
        ]
        paired, open_pos = pair_trades(entries)
        diag = compute_diagnostics(paired, open_pos, entries)
        assert diag["behavioral_diagnostics"]["disposition_effect"]["detected"] is True

    def test_bearish_market_entries(self):
        entries = [
            _make_bias("bearish", "2025-03-14"),
            _make_bias("bearish", "2025-03-15"),
            _make_bias("bearish", "2025-03-16"),
            _make_open(code="000001", price=10.0, shares=1000, date="2025-03-14"),
            _make_close(code="000001", price=9.0, shares=1000, pnl=-1000.0,
                        pnl_pct=-10.0, date="2025-03-20"),
            _make_open(code="000002", price=20.0, shares=1000, date="2025-03-15"),
            _make_close(code="000002", price=18.0, shares=1000, pnl=-2000.0,
                        pnl_pct=-10.0, date="2025-03-21"),
            _make_open(code="000003", price=30.0, shares=1000, date="2025-03-16"),
            _make_close(code="000003", price=27.0, shares=1000, pnl=-3000.0,
                        pnl_pct=-10.0, date="2025-03-22"),
        ]
        paired, open_pos = pair_trades(entries)
        diag = compute_diagnostics(paired, open_pos, entries)
        assert diag["behavioral_diagnostics"]["bearish_market_entries"]["detected"] is True

    def test_overtrading(self):
        entries = []
        for i in range(6):
            entries.append(_make_open(code=f"6000{i:02d}", price=10.0 + i,
                                       shares=100, date="2025-03-14"))
            entries.append(_make_close(code=f"6000{i:02d}", price=11.0 + i,
                                        shares=100, pnl=100.0, pnl_pct=10.0,
                                        date="2025-03-15"))
        paired, open_pos = pair_trades(entries)
        diag = compute_diagnostics(paired, open_pos, entries)
        assert diag["behavioral_diagnostics"]["overtrading"]["detected"] is True


# ═══════════════════════════════════════════════════════════════════
# _detect_patterns tests
# ═══════════════════════════════════════════════════════════════════

class TestDetectPatterns:
    def test_disposition_effect(self):
        patterns = _detect_patterns(
            paired=[{"dummy": 1}], disp_ratio=2.0, bearish_entries=0, bearish_loss=0,
            deviations=0, max_trades_per_day=3, avg_loser_pnl=500,
            winner_pnl_pcts=[10.0], loser_pnl_pcts=[-5.0],
            strategy_breakdown={},
        )
        names = [p["pattern"] for p in patterns]
        assert "处置效应-亏了不肯卖" in names

    def test_bearish_entries(self):
        patterns = _detect_patterns(
            paired=[{"dummy": 1}], disp_ratio=1.0, bearish_entries=5, bearish_loss=-5000,
            deviations=0, max_trades_per_day=3, avg_loser_pnl=500,
            winner_pnl_pcts=[10.0], loser_pnl_pcts=[-5.0],
            strategy_breakdown={},
        )
        names = [p["pattern"] for p in patterns]
        assert any("弱势" in n for n in names)

    def test_strategy_deviation(self):
        patterns = _detect_patterns(
            paired=[{"dummy": 1}], disp_ratio=1.0, bearish_entries=1, bearish_loss=0,
            deviations=1, max_trades_per_day=3, avg_loser_pnl=500,
            winner_pnl_pcts=[10.0], loser_pnl_pcts=[-5.0],
            strategy_breakdown={},
        )
        names = [p["pattern"] for p in patterns]
        assert any("偏离" in n for n in names)

    def test_overtrading(self):
        patterns = _detect_patterns(
            paired=[{"dummy": 1}], disp_ratio=1.0, bearish_entries=1, bearish_loss=0,
            deviations=0, max_trades_per_day=8, avg_loser_pnl=500,
            winner_pnl_pcts=[10.0], loser_pnl_pcts=[-5.0],
            strategy_breakdown={},
        )
        names = [p["pattern"] for p in patterns]
        assert any("过度" in n for n in names)

    def test_risk_reward_inverted(self):
        patterns = _detect_patterns(
            paired=[{"dummy": 1}], disp_ratio=1.0, bearish_entries=1, bearish_loss=0,
            deviations=0, max_trades_per_day=3, avg_loser_pnl=500,
            winner_pnl_pcts=[2.0], loser_pnl_pcts=[-30.0],
            strategy_breakdown={},
        )
        names = [p["pattern"] for p in patterns]
        assert any("盈亏比" in n for n in names)

    def test_no_patterns(self):
        patterns = _detect_patterns(
            paired=[{"dummy": 1}], disp_ratio=1.0, bearish_entries=1, bearish_loss=0,
            deviations=0, max_trades_per_day=3, avg_loser_pnl=0,
            winner_pnl_pcts=[10.0], loser_pnl_pcts=[-5.0],
            strategy_breakdown={},
        )
        assert patterns == []


# ═══════════════════════════════════════════════════════════════════
# format_for_prompt tests
# ═══════════════════════════════════════════════════════════════════

class TestFormatForPrompt:
    def test_with_data(self, mix_winners_losers):
        paired, open_pos = pair_trades(mix_winners_losers)
        diag = compute_diagnostics(paired, open_pos, mix_winners_losers)
        text = format_for_prompt(diag)
        assert len(text) > 50
        assert "交易" in text

    def test_no_trades(self):
        diag = compute_diagnostics([], [], [])
        text = format_for_prompt(diag)
        assert text == ""


# ═══════════════════════════════════════════════════════════════════
# build_reflection_prompt / format_reflections_for_prompt tests
# ═══════════════════════════════════════════════════════════════════

class TestReflectionPrompt:
    def test_with_patterns(self, mix_winners_losers):
        paired, open_pos = pair_trades(mix_winners_losers)
        diag = compute_diagnostics(paired, open_pos, mix_winners_losers)
        prompt = build_reflection_prompt(diag)
        assert len(prompt) > 50
        assert "交易" in prompt

    def test_no_trades(self):
        diag = compute_diagnostics([], [], [])
        prompt = build_reflection_prompt(diag)
        assert prompt == ""


class TestFormatReflections:
    def test_normal(self):
        text = format_reflections_for_prompt("保持纪律，减少追高")
        assert "保持纪律" in text

    def test_empty(self):
        assert format_reflections_for_prompt("") == ""

    def test_whitespace_only(self):
        assert format_reflections_for_prompt("   ") == ""


# ═══════════════════════════════════════════════════════════════════
# Helper function tests
# ═══════════════════════════════════════════════════════════════════

class TestHoldingDays:
    def test_normal(self):
        pair = {"entry_date": "2025-03-14", "exit_date": "2025-03-20"}
        assert _holding_days(pair) == 6

    def test_minimum_one(self):
        pair = {"entry_date": "2025-03-14", "exit_date": "2025-03-14"}
        assert _holding_days(pair) == 1

    def test_missing_dates(self):
        assert _holding_days({"entry_date": "", "exit_date": ""}) == 1

    def test_parse_error(self):
        assert _holding_days({"entry_date": "bad", "exit_date": "also_bad"}) == 1


class TestFindBaseDate:
    def test_from_date_field(self):
        base = _find_base_date([{"date": "2025-06-15"}])
        assert "2025" in base

    def test_fallback(self):
        base = _find_base_date([{"time": "14:30:00"}])
        assert len(base) == 10  # YYYY-MM-DD format

    def test_empty(self):
        base = _find_base_date([])
        assert len(base) == 10


class TestEntryDate:
    def test_from_date(self):
        d = _entry_date({"date": "2025-06-15"}, [], 0)
        assert d == "2025-06-15"

    def test_from_time_with_t(self):
        d = _entry_date({"time": "2025-06-15T14:30:00"}, [], 0)
        assert d == "2025-06-15"

    def test_empty(self):
        d = _entry_date({}, [], 0)
        assert d == ""


# ═══════════════════════════════════════════════════════════════════
# IO function tests
# ═══════════════════════════════════════════════════════════════════

class TestLoadLedger:
    def test_valid(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run"
        run_dir.mkdir(parents=True)
        ledger = run_dir / "ledger.jsonl"
        ledger.write_text(
            json.dumps(_make_open()) + "\n" +
            json.dumps(_make_close()) + "\n",
            encoding="utf-8",
        )
        entries = load_ledger("test_run")
        assert len(entries) == 2

    def test_missing(self):
        entries = load_ledger("nonexistent_run_99999")
        assert entries == []

    def test_malformed_json(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run2"
        run_dir.mkdir(parents=True)
        ledger = run_dir / "ledger.jsonl"
        ledger.write_text(
            json.dumps(_make_open()) + "\n" +
            "not valid json\n" +
            json.dumps(_make_close()) + "\n",
            encoding="utf-8",
        )
        entries = load_ledger("test_run2")
        assert len(entries) == 2  # skips malformed line


class TestSaveDiagnostics:
    def test_creates_files(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run"
        run_dir.mkdir(parents=True)
        diag = compute_diagnostics([], [], [])
        path = save_diagnostics("test_run", diag, sub_c_output="test reflection")
        assert os.path.exists(path)
        shadow_dir = run_dir / "shadow_account"
        jsons = list(shadow_dir.glob("shadow_*.json"))
        assert len(jsons) >= 1

    def test_no_sub_c_output(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run2"
        run_dir.mkdir(parents=True)
        diag = compute_diagnostics([], [], [])
        path = save_diagnostics("test_run2", diag)
        assert os.path.exists(path)


class TestLoadAccumulatedPatterns:
    def test_valid(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        shadow_dir = temp_dir / "data" / "output" / "test_run" / "shadow_account"
        shadow_dir.mkdir(parents=True)
        (shadow_dir / "patterns.json").write_text(
            json.dumps({"patterns": [{"name": "test", "status": "active"}]}),
            encoding="utf-8",
        )
        patterns = load_accumulated_patterns("test_run")
        assert len(patterns) == 1

    def test_missing(self):
        patterns = load_accumulated_patterns("nonexistent_99999")
        assert patterns == []

    def test_malformed(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        shadow_dir = temp_dir / "data" / "output" / "test_run2" / "shadow_account"
        shadow_dir.mkdir(parents=True)
        (shadow_dir / "patterns.json").write_text("{bad json", encoding="utf-8")
        patterns = load_accumulated_patterns("test_run2")
        assert patterns == []


class TestMergePatterns:
    def test_new(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run"
        run_dir.mkdir(parents=True)
        new = [
            {"pattern": "处置效应-亏了不肯卖", "severity": "high",
             "evidence": "disp_ratio=2.0", "suggested_fix": "设止损"},
        ]
        merge_patterns("test_run", new)
        patterns = load_accumulated_patterns("test_run")
        assert len(patterns) == 1
        assert patterns[0]["occurrence_count"] == 1

    def test_update_existing(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run"
        run_dir.mkdir(parents=True)
        # First occurrence
        new = [{"pattern": "过度交易", "severity": "medium", "evidence": "max=8",
                "suggested_fix": "减少"}]
        merge_patterns("test_run", new)
        # Second occurrence
        merge_patterns("test_run", new)
        patterns = load_accumulated_patterns("test_run")
        assert len(patterns) == 1
        assert patterns[0]["occurrence_count"] == 2
        assert patterns[0]["status"] == "active"

    def test_empty(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run"
        run_dir.mkdir(parents=True)
        result = merge_patterns("test_run", [])
        assert result == ""


class TestLoadLatestDiagnostics:
    def test_multiple(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        shadow_dir = temp_dir / "data" / "output" / "test_run" / "shadow_account"
        shadow_dir.mkdir(parents=True)
        (shadow_dir / "shadow_2025-03-14.json").write_text(
            json.dumps({"date": "2025-03-14"}), encoding="utf-8")
        (shadow_dir / "shadow_2025-03-20.json").write_text(
            json.dumps({"date": "2025-03-20"}), encoding="utf-8")
        latest = load_latest_diagnostics("test_run")
        assert latest is not None
        assert latest["date"] == "2025-03-20"

    def test_empty_dir(self):
        result = load_latest_diagnostics("nonexistent_99999")
        assert result is None


class TestCompareRuns:
    def test_detects_changes(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        for rid in ("run_a", "run_b"):
            shadow_dir = temp_dir / "data" / "output" / rid / "shadow_account"
            shadow_dir.mkdir(parents=True)
        # Run A: pattern X and Y
        (temp_dir / "data" / "output" / "run_a" / "shadow_account" / "patterns.json").write_text(
            json.dumps({"patterns": [
                {"name": "X", "status": "active", "occurrence_count": 2},
                {"name": "Y", "status": "active", "occurrence_count": 1},
            ]}), encoding="utf-8")
        # Run B: pattern Y and Z
        (temp_dir / "data" / "output" / "run_b" / "shadow_account" / "patterns.json").write_text(
            json.dumps({"patterns": [
                {"name": "Y", "status": "active", "occurrence_count": 3},
                {"name": "Z", "status": "active", "occurrence_count": 1},
            ]}), encoding="utf-8")
        result = compare_runs("run_a", "run_b")
        assert len(result["resolved"]) == 1  # X resolved
        assert len(result["persistent"]) == 1  # Y persists
        assert len(result["new"]) == 1  # Z new
        assert result["resolved"][0]["name"] == "X"

    def test_both_empty(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        for rid in ("run_a", "run_b"):
            (temp_dir / "data" / "output" / rid / "shadow_account").mkdir(parents=True)
            (temp_dir / "data" / "output" / rid / "shadow_account" / "patterns.json").write_text(
                '{"patterns": []}', encoding="utf-8")
        result = compare_runs("run_a", "run_b")
        assert result["resolved"] == []
        assert result["persistent"] == []
        assert result["new"] == []


# ═══════════════════════════════════════════════════════════════════
# External Agent handoff
# ═══════════════════════════════════════════════════════════════════

class TestPrepareReflectionPrompt:
    def test_insufficient_trades(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run"
        run_dir.mkdir(parents=True)
        shadow_dir = run_dir / "shadow_account"
        shadow_dir.mkdir()
        # Only 2 paired trades (< 4 threshold)
        (shadow_dir / "shadow_2025-03-14.json").write_text(
            json.dumps({"paired_trades_count": 2}), encoding="utf-8")
        result = prepare_reflection_prompt("test_run")
        assert result == ""

    def test_with_sufficient_data(self, temp_dir, monkeypatch):
        import openalphastack.tools.shadow_account as sa
        monkeypatch.setattr(sa, "PROJECT_DIR", str(temp_dir))
        run_dir = temp_dir / "data" / "output" / "test_run"
        run_dir.mkdir(parents=True)
        shadow_dir = run_dir / "shadow_account"
        shadow_dir.mkdir()
        (shadow_dir / "shadow_2025-03-14.json").write_text(
            json.dumps({
                "paired_trades_count": 5, "open_positions_count": 0,
                "summary": {"win_rate": 60.0, "total_pnl": 5000},
                "behavioral_diagnostics": {"disposition_effect": {"detected": False}},
                "recurring_patterns": [],
            }), encoding="utf-8")
        result = prepare_reflection_prompt("test_run")
        assert len(result) > 0
        assert "交易复盘分析师" in result


# ═══════════════════════════════════════════════════════════════════
# Edge case tests
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_zero_division_guards(self):
        paired = [{
            "entry_price": 0, "exit_price": 0, "symbol": "000001",
            "pnl": 0, "shares": 0, "entry_date": "", "exit_date": "",
        }]
        diag = compute_diagnostics(paired, [], [])
        assert diag["summary"]["win_rate"] in (0.0, 0)

    def test_chinese_keyword_detection(self):
        entries = []
        for i in range(6):
            entries.append(_make_open(code=f"6000{i:02d}", price=50.0,
                                       shares=1000, date="2025-03-14"))
        # All losers with Chinese panic keywords
        for i in range(6):
            keywords = ["止损", "恐慌", "紧急平仓", "回调", "扛不住", "波动太大"]
            entries.append(_make_close(code=f"6000{i:02d}", price=45.0,
                                        shares=1000, pnl=-5000.0, pnl_pct=-10.0,
                                        date="2025-03-24",
                                        reasoning=keywords[i]))
        paired, open_pos = pair_trades(entries)
        diag = compute_diagnostics(paired, open_pos, entries)
        assert diag["behavioral_diagnostics"]["strategy_deviation"]["detected"] is True

    def test_fifo_partial_multi_lot(self):
        entries = [
            _make_open(code="000001", price=10.0, shares=500, date="2025-03-14"),
            _make_open(code="000001", price=12.0, shares=300, date="2025-03-15"),
            _make_close(code="000001", price=13.0, shares=700, pnl=1500.0,
                        pnl_pct=25.0, date="2025-03-20"),
        ]
        paired, open_pos = pair_trades(entries)
        # FIFO: 500 from lot1 + 200 from lot2 = 2 paired entries
        assert len(paired) == 2
        assert paired[0]["entry_price"] == 10.0
        assert paired[0]["shares"] == 500
        assert paired[1]["entry_price"] == 12.0
        assert paired[1]["shares"] == 200
        assert len(open_pos) == 1
        assert open_pos[0]["entry_price"] == 12.0
        assert open_pos[0]["remaining_shares"] == 100

    def test_disposition_effect_threshold_exact(self):
        # ratio = 1.5 exactly should NOT trigger
        entries = [
            _make_open(code="000001", price=10.0, shares=1000, date="2025-03-14"),
            _make_close(code="000001", price=11.0, shares=1000, pnl=1000.0,
                        pnl_pct=10.0, date="2025-03-16"),  # held 2 days
            _make_open(code="000002", price=20.0, shares=1000, date="2025-03-14"),
            _make_close(code="000002", price=18.0, shares=1000, pnl=-2000.0,
                        pnl_pct=-10.0, date="2025-03-17"),  # held 3 days, ratio=1.5
        ]
        paired, open_pos = pair_trades(entries)
        diag = compute_diagnostics(paired, open_pos, entries)
        assert diag["behavioral_diagnostics"]["disposition_effect"]["detected"] is False
