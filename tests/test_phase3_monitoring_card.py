"""Tests for build_monitoring_card() and the 监控/面板 command."""
from __future__ import annotations

import json

import pytest
import feishu.bot as feishu_bot_module

from alphaclaude.tools import engine_status
from alphaclaude.app import main as app_main


def _sample_run(**overrides) -> dict:
    base = {
        "run_id": "paper_2026-05-28T10-00-00",
        "mode": "paper",
        "phase": "trading",
        "is_alive": True,
        "data_time": "2026-05-28 10:30:00",
        "engine_meta": {},
        "total_value": 105000.0,
        "total_pnl": 5000.0,
        "total_pnl_pct": 5.0,
        "cash": 60000.0,
        "day_pnl": 1200.0,
        "day_pnl_pct": 1.15,
        "max_drawdown": 3.5,
        "holdings": {"600519": {"shares": 100, "avg_cost": 400.0, "current_price": 450.0}},
        "trade_count": 5,
        "today_trades": 2,
        "win_rate": 60.0,
        "total_commission": 25.0,
        "total_stamp_duty": 0.0,
        "cooldown_count": 0,
        "cooldown_codes": [],
        "stopped_out_count": 0,
        "stopped_out_codes": [],
    }
    base.update(overrides)
    return base


class TestBuildMonitoringCard:
    def test_structure(self):
        card = engine_status.build_monitoring_card(runs=[_sample_run()])
        assert card["config"] == {"wide_screen_mode": True}
        assert card["header"]["template"] == "wathet"
        assert "引擎监控面板" in card["header"]["title"]["content"]
        assert len(card["elements"]) >= 2

    def test_empty_runs(self):
        card = engine_status.build_monitoring_card(runs=[])
        assert len(card["elements"]) >= 2
        last = card["elements"][-1]
        assert "当前无活跃" in last["text"]["content"]

    def test_summary_values(self):
        card = engine_status.build_monitoring_card(runs=[_sample_run()])
        summary_text = card["elements"][0]["text"]["content"]
        assert "105,000" in summary_text

    def test_run_mode_and_status(self):
        card = engine_status.build_monitoring_card(runs=[_sample_run()])
        # Find the run div by searching for mode label
        run_contents = [
            e["text"]["content"]
            for e in card["elements"]
            if e.get("tag") == "div"
            and "text" in e
            and ("PAPER" in e["text"]["content"] or "BACKTEST" in e["text"]["content"] or "LIVE" in e["text"]["content"])
        ]
        assert len(run_contents) >= 1
        assert "盘中交易" in run_contents[0]

    def test_stopped_run_black_circle(self):
        card = engine_status.build_monitoring_card(
            runs=[_sample_run(is_alive=False, phase="已停止")]
        )
        all_divs = [
            e["text"]["content"]
            for e in card["elements"]
            if e.get("tag") == "div" and "text" in e
        ]
        run_content = [c for c in all_divs if "PAPER" in c][0]
        assert "⚫" in run_content

    def test_backtest_progress(self):
        card = engine_status.build_monitoring_card(runs=[
            _sample_run(mode="backtest", is_alive=True,
                        engine_meta={"progress": {"current_day": 42, "total_days": 120}}),
        ])
        all_divs = [
            e["text"]["content"]
            for e in card["elements"]
            if e.get("tag") == "div" and "text" in e
        ]
        run_content = [c for c in all_divs if "BACKTEST" in c][0]
        assert "进度 42/120" in run_content

    def test_backtest_no_day_pnl_line(self):
        card = engine_status.build_monitoring_card(
            runs=[_sample_run(mode="backtest", day_pnl=1000, day_pnl_pct=1.0)]
        )
        all_divs = [
            e["text"]["content"]
            for e in card["elements"]
            if e.get("tag") == "div" and "text" in e
        ]
        run_content = [c for c in all_divs if "BACKTEST" in c][0]
        assert "今日:" not in run_content

    def test_cooldown_codes(self):
        card = engine_status.build_monitoring_card(
            runs=[_sample_run(cooldown_count=2, cooldown_codes=["600519", "000858"])]
        )
        all_divs = [
            e["text"]["content"]
            for e in card["elements"]
            if e.get("tag") == "div" and "text" in e
        ]
        run_content = [c for c in all_divs if "PAPER" in c][0]
        assert "冷却: 600519, 000858" in run_content

    def test_stopped_out_codes(self):
        card = engine_status.build_monitoring_card(
            runs=[_sample_run(stopped_out_count=1, stopped_out_codes=["300263"])]
        )
        all_divs = [
            e["text"]["content"]
            for e in card["elements"]
            if e.get("tag") == "div" and "text" in e
        ]
        run_content = [c for c in all_divs if "PAPER" in c][0]
        assert "止损: 300263" in run_content

    def test_drawdown(self):
        card = engine_status.build_monitoring_card(
            runs=[_sample_run(max_drawdown=8.5)]
        )
        all_divs = [
            e["text"]["content"]
            for e in card["elements"]
            if e.get("tag") == "div" and "text" in e
        ]
        run_content = [c for c in all_divs if "PAPER" in c][0]
        assert "最大回撤 -8.50%" in run_content

    def test_no_drawdown_when_zero(self):
        card = engine_status.build_monitoring_card(
            runs=[_sample_run(max_drawdown=0.0)]
        )
        all_divs = [
            e["text"]["content"]
            for e in card["elements"]
            if e.get("tag") == "div" and "text" in e
        ]
        run_content = [c for c in all_divs if "PAPER" in c][0]
        assert "最大回撤" not in run_content


class TestDashboardCommand:
    """Tests for /面板 /监控 /dashboard commands in _handle_command.

    _handle_command imports build_monitoring_card and send_card inline,
    so we monkeypatch the source modules: engine_status and feishu.bot.
    """

    def test_returns_none(self, monkeypatch):
        monkeypatch.setattr(engine_status, "build_monitoring_card", lambda: {"mock": "card"})
        monkeypatch.setattr(feishu_bot_module, "send_card", lambda chat_id, card: {"code": 0})

        reply = app_main._handle_command("chat-1", "p2p", "面板")
        assert reply is None

    def test_alias_monitor(self, monkeypatch):
        monkeypatch.setattr(engine_status, "build_monitoring_card", lambda: {"mock": "card"})
        monkeypatch.setattr(feishu_bot_module, "send_card", lambda chat_id, card: {"code": 0})

        reply = app_main._handle_command("chat-2", "group", "监控")
        assert reply is None

    def test_all_aliases(self, monkeypatch):
        calls = []
        monkeypatch.setattr(engine_status, "build_monitoring_card", lambda: {"mock": "card"})

        def fake_send(chat_id, card):
            calls.append(chat_id)
            return {"code": 0}

        monkeypatch.setattr(feishu_bot_module, "send_card", fake_send)

        for cmd in ("/dashboard", "/监控", "/面板", "/总览", "dashboard"):
            reply = app_main._handle_command("chat-1", "p2p", cmd)
            assert reply is None

        assert len(calls) == 5

    def test_exception_returns_error_text(self, monkeypatch):
        def raise_err(*_a, **_kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(engine_status, "build_monitoring_card", raise_err)

        reply = app_main._handle_command("chat-1", "p2p", "监控")
        assert "无法生成监控面板" in reply
        assert "boom" in reply


class TestSendCard:
    """Tests for feishu.bot.send_card()."""

    @pytest.fixture(autouse=True)
    def _mock_auth(self, monkeypatch):
        """Stub out headers so we don't hit the real Feishu auth API."""
        monkeypatch.setattr(
            feishu_bot_module, "_headers",
            lambda: {"Authorization": "Bearer test-token", "Content-Type": "application/json"},
        )

    def test_format(self, monkeypatch):
        import httpx

        calls = []

        class FakeResp:
            status_code = 200
            @staticmethod
            def json():
                return {"code": 0, "msg": "ok"}

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: calls.append(kw) or FakeResp())

        result = feishu_bot_module.send_card("test-chat-id", {"header": {"title": "Test"}})
        assert result["code"] == 0
        assert len(calls) == 1
        body = calls[0]["json"]
        assert body["receive_id"] == "test-chat-id"
        assert body["msg_type"] == "interactive"
        assert json.loads(body["content"])["header"]["title"] == "Test"

    def test_with_root_message_id(self, monkeypatch):
        import httpx

        calls = []

        class FakeResp:
            status_code = 200
            @staticmethod
            def json():
                return {"code": 0}

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: calls.append(kw) or FakeResp())

        feishu_bot_module.send_card("chat-1", {"header": {}}, root_message_id="root-msg-id")
        body = calls[0]["json"]
        assert body["root_id"] == "root-msg-id"

    def test_no_root_id_by_default(self, monkeypatch):
        import httpx

        calls = []

        class FakeResp:
            status_code = 200
            @staticmethod
            def json():
                return {"code": 0}

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: calls.append(kw) or FakeResp())

        feishu_bot_module.send_card("chat-1", {"header": {}})
        body = calls[0]["json"]
        assert "root_id" not in body
        body = calls[0]["json"]
        assert body["root_id"] == "root-msg-id"

    def test_no_root_id_by_default(self, monkeypatch):
        import httpx

        calls = []

        class FakeResp:
            status_code = 200
            @staticmethod
            def json():
                return {"code": 0}

        monkeypatch.setattr(httpx, "post", lambda *a, **kw: calls.append(kw) or FakeResp())

        feishu_bot_module.send_card("chat-1", {"header": {}})
        body = calls[0]["json"]
        assert "root_id" not in body
