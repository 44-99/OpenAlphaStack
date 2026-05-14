from __future__ import annotations

from alphaclaude.app import main as app_main


def test_streaming_reply_sends_final_reply_when_text_update_is_rejected(monkeypatch):
    replies: list[tuple[str, str]] = []
    updates: list[tuple[str, str]] = []

    def fake_reply(message_id: str, text: str) -> dict:
        replies.append((message_id, text))
        return {"code": 0, "data": {"message_id": "stream_message_id"}}

    def fake_update(message_id: str, text: str) -> dict:
        updates.append((message_id, text))
        return {"code": 230001, "msg": "This message is NOT a card."}

    monkeypatch.setattr(app_main, "reply_message", fake_reply)
    monkeypatch.setattr(app_main, "update_message", fake_update)
    monkeypatch.setattr(
        app_main,
        "ask_claude_stream",
        lambda _prompt, session_id: iter(["分析", "完成"]),
    )
    monkeypatch.setattr(app_main, "STREAM_UPDATE_MS", 0)

    app_main._reply_streaming(
        "分析一下600584",
        session_id="session-1",
        orig_message_id="orig_message_id",
        chat_id="chat-1",
    )

    assert replies[0] == ("orig_message_id", "正在分析，请稍候...")
    assert updates
    assert replies[-1] == ("orig_message_id", "分析完成")
