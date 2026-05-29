"""Tool metadata registry — decorator for annotating CLI tools.

Usage in a tool module:
    from alphaclaude.tools._registry import tool_meta
    tool_meta(
        name="quote",
        category="行情",
        description="个股实时行情或大盘指数",
        usage="python -m alphaclaude.tools.quote <code>",
        scenario="获取价格、涨跌幅、换手率、量比、PE/PB",
    )

The generate_tool_table.py script scans these annotations to auto-generate
the CLAUDE.md tool table.
"""

from __future__ import annotations

import sys as _sys
from typing import Any

_registry: dict[str, dict[str, str]] = {}


def tool_meta(
    name: str,
    category: str,
    description: str,
    usage: str,
    scenario: str,
    **kwargs: Any,
) -> None:
    """Register tool metadata for the calling module.

    Call this at module level (not inside a function). The decorator
    automatically detects the caller's module name via stack inspection.
    """
    import inspect

    frame = inspect.currentframe()
    try:
        caller_frame = frame.f_back
        caller_module = caller_frame.f_globals.get("__name__", "")
    finally:
        del frame

    key = caller_module or name
    _registry[key] = {
        "name": name,
        "category": category,
        "description": description,
        "usage": usage,
        "scenario": scenario,
        **kwargs,
    }


def get_all_meta() -> dict[str, dict[str, str]]:
    """Return all registered tool metadata."""
    return dict(_registry)


def scan_tools(package_prefix: str = "alphaclaude.tools.") -> dict[str, dict[str, str]]:
    """Import all tool modules under package_prefix to populate the registry.

    Call this before get_all_meta() if modules haven't been imported yet.
    """
    import pkgutil
    import importlib

    # Import the package itself
    try:
        pkg = importlib.import_module(package_prefix.rstrip("."))
    except ImportError:
        return {}

    for _, modname, is_pkg in pkgutil.iter_modules(pkg.__path__, prefix=package_prefix):
        if is_pkg or modname.startswith("_"):
            continue
        try:
            if modname in _sys.modules:
                importlib.reload(_sys.modules[modname])
            else:
                importlib.import_module(modname)
        except Exception:
            pass

    return dict(_registry)
