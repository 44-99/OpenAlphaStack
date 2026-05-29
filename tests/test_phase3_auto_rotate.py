"""Tests for session auto-rotation (_check_context_budget, _trigger_rotate)."""
from __future__ import annotations

import pytest
import claude as claude_module
from alphaclaude.app import main as app_main


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Isolate sessions and mock disk I/O. Rotation config at defaults."""
    monkeypatch.setattr(app_main, "_sessions", {})
    monkeypatch.setattr(app_main, "_load_sessions", lambda: None)
    monkeypatch.setattr(app_main, "_save_sessions", lambda: None)
    monkeypatch.setattr(app_main.memory, "_consolidate_session", lambda conv_id: None)
    monkeypatch.setattr(app_main, "AUTO_ROTATE_ENABLED", True)
    monkeypatch.setattr(app_main, "CONTEXT_WINDOW_TOKENS", 180000)
    monkeypatch.setattr(app_main, "ROTATE_THRESHOLD", 0.8)
    monkeypatch.setattr(app_main, "MAX_TURNS_BEFORE_ROTATE", 15)
    # Default: no token usage info (forces token check to be skipped)
    monkeypatch.setattr(claude_module, "get_last_token_usage", lambda: {})


def _make_session(sid="s1", stype="dm", turn_count=0):
    return {"session_id": sid, "type": stype, "label": "", "reply_mode": "full", "turn_count": turn_count}


def test_check_budget_increments_turn_count():
    s = _make_session(turn_count=3)
    app_main._sessions["conv-1"] = s

    result = app_main._check_context_budget("conv-1")

    assert result is False
    assert s["turn_count"] == 4


def test_check_budget_no_session_returns_false():
    assert app_main._check_context_budget("nonexistent") is False


def test_check_budget_disabled_by_config(monkeypatch):
    monkeypatch.setattr(app_main, "AUTO_ROTATE_ENABLED", False)
    s = _make_session(turn_count=14)
    app_main._sessions["conv-1"] = s

    result = app_main._check_context_budget("conv-1")

    assert result is False
    assert s["turn_count"] == 14  # not incremented


def test_check_budget_triggers_on_turn_limit():
    s = _make_session(turn_count=14)
    app_main._sessions["conv-1"] = s

    result = app_main._check_context_budget("conv-1")

    assert result is True
    assert s["turn_count"] == 15


def test_check_budget_triggers_on_token_threshold(monkeypatch):
    s = _make_session(turn_count=3)
    app_main._sessions["conv-1"] = s
    monkeypatch.setattr(claude_module, "get_last_token_usage", lambda: {"input_tokens": 150000})

    result = app_main._check_context_budget("conv-1")

    assert result is True


def test_check_budget_no_trigger_below_token_threshold():
    s = _make_session(turn_count=3)
    app_main._sessions["conv-1"] = s

    result = app_main._check_context_budget("conv-1")

    assert result is False


def test_check_budget_handles_empty_usage():
    s = _make_session(turn_count=3)
    app_main._sessions["conv-1"] = s

    result = app_main._check_context_budget("conv-1")
    assert result is False


def test_trigger_rotate_resets_session():
    old_sid = "old-session-uuid"
    s = {"session_id": old_sid, "type": "dm", "label": "", "reply_mode": "compact", "turn_count": 15}
    app_main._sessions["conv-1"] = s

    app_main._trigger_rotate("conv-1")

    new_session = app_main._sessions["conv-1"]
    assert new_session["session_id"] != old_sid
    assert new_session["turn_count"] == 0
    assert new_session["reply_mode"] == "compact"
    assert new_session["type"] == "dm"


def test_trigger_rotate_calls_consolidate_memory(monkeypatch):
    calls = []
    monkeypatch.setattr(app_main.memory, "_consolidate_session", lambda conv_id: calls.append(conv_id))
    app_main._sessions["conv-1"] = _make_session()

    app_main._trigger_rotate("conv-1")

    assert calls == ["conv-1"]


def test_rotate_preserves_reply_mode():
    s = {"session_id": "old-id", "type": "dm", "label": "Me", "reply_mode": "quiet", "turn_count": 20}
    app_main._sessions["conv-1"] = s

    app_main._trigger_rotate("conv-1")

    assert app_main._sessions["conv-1"]["reply_mode"] == "quiet"
