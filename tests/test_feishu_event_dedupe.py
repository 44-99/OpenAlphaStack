from __future__ import annotations

import json
import time

from alphaclaude.app import main as app_main


def _message_event(
    event_id: str = "event-1",
    message_id: str = "message-1",
    create_time: str | None = None,
) -> dict:
    header = {
        "event_id": event_id,
        "event_type": "im.message.receive_v1",
    }
    if create_time is not None:
        header["create_time"] = create_time
    return {
        "header": header,
        "event": {
            "sender": {
                "sender_id": {
                    "open_id": "user-1",
                },
            },
            "message": {
                "message_id": message_id,
                "chat_id": "chat-1",
                "chat_type": "p2p",
                "content": json.dumps({"text": "/status"}),
            },
        },
    }


def _clear_event_dedupe() -> None:
    with app_main._processed_event_lock:
        app_main._processed_event_keys.clear()


def test_sdk_event_dedupe_skips_repeated_event_id(monkeypatch):
    _clear_event_dedupe()
    processed: list[dict] = []

    monkeypatch.setattr(app_main, "_process_message", lambda event: processed.append(event))

    event = _message_event(event_id="repeat-event", message_id="message-a")
    app_main._handle_sdk_event(event)
    app_main._handle_sdk_event(event)

    assert len(processed) == 1
    assert processed[0]["message_id"] == "message-a"


def test_sdk_event_dedupe_falls_back_to_message_id(monkeypatch):
    _clear_event_dedupe()
    processed: list[dict] = []

    monkeypatch.setattr(app_main, "_process_message", lambda event: processed.append(event))

    event = _message_event(event_id="", message_id="repeat-message")
    app_main._handle_sdk_event(event)
    app_main._handle_sdk_event(event)

    assert len(processed) == 1
    assert processed[0]["message_id"] == "repeat-message"


def test_sdk_event_dedupe_allows_new_events(monkeypatch):
    _clear_event_dedupe()
    processed: list[dict] = []

    monkeypatch.setattr(app_main, "_process_message", lambda event: processed.append(event))

    app_main._handle_sdk_event(_message_event(event_id="event-a", message_id="message-a"))
    app_main._handle_sdk_event(_message_event(event_id="event-b", message_id="message-b"))

    assert [event["message_id"] for event in processed] == ["message-a", "message-b"]


def test_sdk_event_skips_stale_message_events(monkeypatch):
    _clear_event_dedupe()
    processed: list[dict] = []
    old_create_time = str(int((time.time() - app_main._EVENT_STALE_SECONDS - 1) * 1000))

    monkeypatch.setattr(app_main, "_process_message", lambda event: processed.append(event))

    app_main._handle_sdk_event(
        _message_event(
            event_id="old-event",
            message_id="old-message",
            create_time=old_create_time,
        )
    )

    assert processed == []


def test_exact_command_dispatches_command_work_to_background(monkeypatch):
    started: list[tuple[object, tuple, bool]] = []
    command_calls: list[tuple[str, str, str]] = []
    replies: list[tuple[str, str]] = []

    class FakeThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append((self.target, self.args, bool(self.daemon)))

    monkeypatch.setattr(app_main.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        app_main,
        "_handle_command",
        lambda chat_id, chat_type, text: command_calls.append((chat_id, chat_type, text)) or "状态正常",
    )
    monkeypatch.setattr(app_main, "reply_message", lambda message_id, text: replies.append((message_id, text)))

    app_main._process_message(
        {
            "chat_id": "chat-1",
            "chat_type": "p2p",
            "sender_id": "user-1",
            "text": "/status",
            "message_id": "message-1",
        }
    )

    assert command_calls == []
    assert len(started) == 1
    target, args, daemon = started[0]
    assert target is app_main._reply_exact_command
    assert args == ("chat-1", "p2p", "/status", "message-1")
    assert daemon is True

    target(*args)

    assert command_calls == [("chat-1", "p2p", "/status")]
    assert replies == [("message-1", "状态正常")]
