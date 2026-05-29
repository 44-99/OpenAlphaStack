"""Tests for tool_meta() registry, get_all_meta(), and scan_tools()."""
from __future__ import annotations

import pytest
from alphaclaude.tools._registry import tool_meta, get_all_meta, scan_tools, _registry


@pytest.fixture(autouse=True)
def _clear_registry():
    _registry.clear()
    yield
    _registry.clear()


def test_tool_meta_registers_with_caller_module_name():
    tool_meta(
        name="test_tool",
        category="测试",
        description="A test tool",
        usage="test usage",
        scenario="test scenario",
    )
    all_meta = get_all_meta()
    assert len(all_meta) == 1
    key = list(all_meta.keys())[0]
    assert all_meta[key]["name"] == "test_tool"
    assert all_meta[key]["category"] == "测试"


def test_tool_meta_overwrites_same_module_key():
    tool_meta(name="dup", category="A", description="d", usage="u", scenario="s")
    first_key = list(get_all_meta().keys())[0]

    tool_meta(name="dup", category="B", description="d", usage="u", scenario="s")
    assert len(get_all_meta()) == 1  # still 1, overwritten
    assert get_all_meta()[first_key]["category"] == "B"


def test_get_all_meta_returns_copy():
    tool_meta(name="t1", category="C", description="d", usage="u", scenario="s")
    copy1 = get_all_meta()
    copy1["fake"] = {"name": "added later"}
    assert "fake" not in _registry


def test_tool_meta_extra_kwargs_preserved():
    tool_meta(
        name="extra_test",
        category="测试",
        description="desc",
        usage="usage",
        scenario="scenario",
        author="test-author",
        version="1.0",
    )
    entry = list(get_all_meta().values())[0]
    assert entry["author"] == "test-author"
    assert entry["version"] == "1.0"


def test_scan_tools_discovers_annotated_modules():
    meta = scan_tools()

    assert len(meta) >= 5
    names = {v["name"] for v in meta.values()}
    assert names >= {"quote", "technical", "flow", "news", "screen"}


def test_scan_tools_skips_private_modules():
    meta = scan_tools()

    for key in meta:
        module_name = key.split(".")[-1]
        assert not module_name.startswith("_"), f"Private module {module_name} should be skipped"


def test_scan_tools_each_entry_has_required_fields():
    meta = scan_tools()

    for info in meta.values():
        assert "name" in info
        assert "category" in info
        assert "description" in info
        assert "usage" in info
        assert "scenario" in info


def test_scan_tools_imports_quote_module():
    meta = scan_tools()

    quote_entry = None
    for v in meta.values():
        if v["name"] == "quote":
            quote_entry = v
            break
    assert quote_entry is not None
    assert quote_entry["category"] == "行情"
    assert "python -m alphaclaude.tools.quote" in quote_entry["usage"]


def test_scan_tools_imports_screen_module():
    meta = scan_tools()

    screen_entry = None
    for v in meta.values():
        if v["name"] == "screen":
            screen_entry = v
            break
    assert screen_entry is not None
    assert screen_entry["category"] == "信息与筛选"
    assert "screen -s" in screen_entry["usage"]


def test_scan_tools_handles_unknown_package():
    """scan_tools with a non-existent package returns empty dict."""
    meta = scan_tools("nonexistent.package.")
    assert meta == {}
