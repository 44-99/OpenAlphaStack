from __future__ import annotations

from alphaclaude.tools import llm_client


def test_call_with_tool_safe_returns_structured_result(monkeypatch):
    monkeypatch.setattr(llm_client, "call_with_tool", lambda *args, **kwargs: [{"ok": True}])

    result = llm_client.call_with_tool_safe("prompt", [{"name": "tool"}])

    assert result == [{"ok": True}]


def test_call_with_tool_safe_uses_text_parser_when_tool_returns_empty(monkeypatch):
    monkeypatch.setattr(llm_client, "call_with_tool", lambda *args, **kwargs: [])
    monkeypatch.setattr(llm_client, "call_text", lambda *args, **kwargs: '{"ok": true}')

    result = llm_client.call_with_tool_safe(
        "prompt",
        [{"name": "tool"}],
        fallback_parser=lambda text: [{"parsed": text}],
    )

    assert result == [{"parsed": '{"ok": true}'}]


def test_call_with_tool_safe_uses_text_parser_when_tool_raises(monkeypatch):
    def raise_tool(*_args, **_kwargs):
        raise RuntimeError("tool failure")

    monkeypatch.setattr(llm_client, "call_with_tool", raise_tool)
    monkeypatch.setattr(llm_client, "call_text", lambda *args, **kwargs: "fallback text")

    result = llm_client.call_with_tool_safe(
        "prompt",
        [{"name": "tool"}],
        fallback_parser=lambda text: [{"fallback": text}],
    )

    assert result == [{"fallback": "fallback text"}]


def test_call_with_tool_safe_returns_empty_when_fallback_fails(monkeypatch):
    def raise_tool(*_args, **_kwargs):
        raise RuntimeError("tool failure")

    def raise_text(*_args, **_kwargs):
        raise RuntimeError("text failure")

    monkeypatch.setattr(llm_client, "call_with_tool", raise_tool)
    monkeypatch.setattr(llm_client, "call_text", raise_text)

    result = llm_client.call_with_tool_safe(
        "prompt",
        [{"name": "tool"}],
        fallback_parser=lambda text: [{"fallback": text}],
    )

    assert result == []
