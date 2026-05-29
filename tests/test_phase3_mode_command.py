"""Tests for /mode full/compact/quiet command and prompt injection."""
from __future__ import annotations

import pytest
from alphaclaude.app import main as app_main


@pytest.fixture(autouse=True)
def _clear_sessions(monkeypatch):
    """Isolate session state per test."""
    monkeypatch.setattr(app_main, "_sessions", {})
    monkeypatch.setattr(app_main, "_load_sessions", lambda: None)
    monkeypatch.setattr(app_main, "_save_sessions", lambda: None)


def test_mode_switch_full_via_slash():
    # p2p session key = chat_id + "_" + sender_id
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "compact", "turn_count": 0}
    reply = app_main._handle_command("chat-1", "p2p", "/mode full", "user1")
    assert reply == "已切换为 完整模式。"
    assert app_main._sessions["chat-1_user1"]["reply_mode"] == "full"


def test_mode_switch_compact_via_slash():
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "full", "turn_count": 0}
    reply = app_main._handle_command("chat-1", "p2p", "/mode compact", "user1")
    assert reply == "已切换为 简洁模式。"
    assert app_main._sessions["chat-1_user1"]["reply_mode"] == "compact"


def test_mode_switch_quiet_via_slash():
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "full", "turn_count": 0}
    reply = app_main._handle_command("chat-1", "p2p", "/mode quiet", "user1")
    assert reply == "已切换为 极简模式。"
    assert app_main._sessions["chat-1_user1"]["reply_mode"] == "quiet"


def test_mode_switch_without_slash():
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "full", "turn_count": 0}
    reply = app_main._handle_command("chat-1", "p2p", "mode compact", "user1")
    assert reply == "已切换为 简洁模式。"


def test_mode_chinese_alias_full():
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "compact", "turn_count": 0}
    assert app_main._handle_command("chat-1", "p2p", "/mode 完整", "user1") == "已切换为 完整模式。"
    assert app_main._handle_command("chat-1", "p2p", "/mode 详细", "user1") == "已切换为 完整模式。"


def test_mode_chinese_alias_compact():
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "full", "turn_count": 0}
    assert app_main._handle_command("chat-1", "p2p", "/mode 简洁", "user1") == "已切换为 简洁模式。"
    assert app_main._handle_command("chat-1", "p2p", "/mode 精简", "user1") == "已切换为 简洁模式。"


def test_mode_chinese_alias_quiet():
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "full", "turn_count": 0}
    assert app_main._handle_command("chat-1", "p2p", "/mode 极简", "user1") == "已切换为 极简模式。"
    assert app_main._handle_command("chat-1", "p2p", "/mode 一句话", "user1") == "已切换为 极简模式。"


def test_mode_invalid_returns_usage():
    reply = app_main._handle_command("chat-1", "p2p", "/mode invalid_mode", "user1")
    assert "用法: /mode full|compact|quiet" in reply


def test_mode_bare_shows_current():
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "compact", "turn_count": 0}
    reply = app_main._handle_command("chat-1", "p2p", "/mode", "user1")
    assert "简洁模式" in reply


def test_mode_bare_defaults_to_full_when_no_session():
    reply = app_main._handle_command("chat-1", "p2p", "/mode", "user1")
    assert "完整模式" in reply


def test_mode_p2p_uses_compound_key():
    app_main._sessions["chat-1_user1"] = {"session_id": "s1", "type": "dm", "reply_mode": "full", "turn_count": 0}
    reply = app_main._handle_command("chat-1", "p2p", "/mode compact", "user1")
    assert reply == "已切换为 简洁模式。"
    assert app_main._sessions["chat-1_user1"]["reply_mode"] == "compact"


def test_mode_group_uses_chat_only_key():
    app_main._sessions["chat-1"] = {"session_id": "s1", "type": "group", "reply_mode": "full", "turn_count": 0}
    reply = app_main._handle_command("chat-1", "group", "/mode compact", "")
    assert reply == "已切换为 简洁模式。"
    assert app_main._sessions["chat-1"]["reply_mode"] == "compact"


def test_mode_no_existing_session_no_crash():
    reply = app_main._handle_command("chat-1", "p2p", "/mode compact", "nobody")
    assert "已切换为 简洁模式" in reply
