"""Tool output compression — compact, LLM-friendly summaries of JSON tool results.

Internal utility, not a user-facing tool. Import by pipeline.py and CLAUDE.md
tool-combination patterns that need to stay within context-window budgets.
"""

from __future__ import annotations

import json
from typing import Any

# ── Per-tool field schemas ────────────────────────────────────────
# Each entry: {"top_level": [...scalar keys], "list_field": "...",
#              "item_fields": [...per-item keys], "max_items": int|None}

_COMPRESSION_SCHEMA: dict[str, dict[str, Any]] = {
    "screen": {
        "top_level": ["strategy", "total_matched", "top_n"],
        "list_field": "results",
        "item_fields": ["代码", "名称", "最新价", "涨跌幅", "换手率", "量比", "市盈率-动态", "成交额"],
        "max_items": 10,
        "labels": {"市盈率-动态": "PE"},
    },
    "quote": {
        "top_level": [
            "code", "name", "price", "change_pct", "turnover_rate",
            "volume_ratio", "pe", "pb", "amplitude", "high", "low", "open",
            "volume", "amount",
        ],
        "max_items": None,
    },
    "market_overview": {
        "top_level": [],
        "list_field": "indices",
        "item_fields": ["名称", "价格", "涨跌幅"],
        "max_items": 6,
        "labels": {"价格": "price", "涨跌幅": "chg%"},
    },
    "technical": {
        "top_level": ["code", "name"],
        "list_field": None,
        "item_fields": [],
        "max_items": None,
        "nested": {
            "ma": ["MA5", "MA10", "MA20", "vs_ma5"],
            "macd": ["DIF", "DEA", "signal", "crossover"],
            "rsi": ["value", "zone"],
            "kdj": ["K", "D", "J", "zone"],
            "bollinger": ["upper", "mid", "lower", "price_position", "width_pct"],
            "volume_price": ["signal", "volume_ratio"],
        },
    },
    "trend": {
        "top_level": ["code"],
        "list_field": None,
        "item_fields": [],
        "max_items": None,
        "nested": {
            "alignment": ["status", "ma_positions"],
            "deviation": ["ma5.deviation_pct", "ma5.zone", "ma20.deviation_pct"],
            "trend_status": ["price_vs_ma20_pct", "ma60_slope_pct"],
            "crossovers": ["recent"],
        },
    },
    "signal_detector": {
        "top_level": ["code"],
        "list_field": None,
        "item_fields": [],
        "max_items": None,
        "nested": {
            "golden_cross": ["detected", "buy_price", "stop_loss"],
            "volume_breakout": ["detected", "buy_price", "stop_loss"],
            "shrink_pullback": ["detected", "buy_price", "stop_loss"],
            "bottom_volume": ["detected", "buy_price", "stop_loss"],
            "one_yang_three_yin": ["detected", "buy_price", "stop_loss"],
        },
    },
    "sentiment": {
        "top_level": ["code"],
        "nested": {
            "sentiment": ["stage", "advice", "total_score"],
            "turnover_trend": ["heat_level"],
            "volume_trend": ["vs_60d_pct"],
        },
        "max_items": None,
    },
    "news": {
        "top_level": ["sentiment"],
        "list_field": "news",
        "item_fields": ["title", "time", "source"],
        "max_items": 8,
    },
    "pivot": {
        "top_level": ["code"],
        "nested": {
            "box_range": ["zone", "action", "box_top", "box_bottom", "stop_loss"],
            "zhongshu": ["direction", "zg", "zd"],
            "pivot_summary": ["support_clusters", "resistance_clusters"],
        },
        "max_items": None,
    },
    "fundamental": {
        "top_level": [
            "code", "name", "price", "pe", "pb", "roe", "revenue_growth",
            "net_margin", "debt_ratio", "eps",
        ],
        "max_items": None,
    },
    "fibonacci": {
        "top_level": [
            "code", "primary_trend", "swing_high", "swing_low",
        ],
        "nested": {
            "retracement_levels": ["0.382", "0.5", "0.618"],
            "extension_levels": ["1.272", "1.618"],
        },
        "max_items": None,
    },
    "flow": {
        "top_level": [
            "code", "name", "main_net_flow", "signal",
        ],
        "list_field": None,
        "item_fields": [],
        "max_items": None,
    },
    "north_flow": {
        "top_level": [
            "net_inflow", "trend", "sh_net_buy", "sz_net_buy",
        ],
        "max_items": None,
    },
}


def _fmt_val(v: Any) -> str:
    """Format a scalar value for compact display."""
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v[:3])
    return str(v)


def _extract_nested(data: dict, nested: dict) -> list[str]:
    """Extract fields from nested sub-dicts. Returns formatted lines."""
    lines = []
    for section, keys in nested.items():
        if section not in data:
            continue
        sub = data[section]
        if not isinstance(sub, dict):
            continue
        parts = []
        for k in keys:
            # Support dotted path (e.g., "ma5.deviation_pct")
            if "." in k:
                sub_key, leaf = k.split(".", 1)
                inner = sub.get(sub_key)
                if isinstance(inner, dict):
                    v = inner.get(leaf)
                    if v is not None:
                        parts.append(f"{leaf}={_fmt_val(v)}")
            elif k in sub:
                v = sub[k]
                if v is not None and v != "":
                    parts.append(f"{k}={_fmt_val(v)}")
        if parts:
            lines.append(f"  {section}: " + ", ".join(parts))
    return lines


def compress_output(
    data: dict, tool_name: str, *, max_items: int = 10
) -> str:
    """Compress a single tool's JSON output to compact, LLM-friendly text.

    Args:
        data: Raw tool output dict (as returned by the tool function).
        tool_name: Registry key matching the schema (e.g. "screen", "quote").
        max_items: Max list items to render (applies per-tool, clamps to schema).

    Returns:
        Newline-separated summary suitable for direct prompt injection.
        Error-containing dicts return ``[tool_name error] message``.
        Unknown tools fall back to compact JSON (first 500 chars).
    """
    if not isinstance(data, dict):
        return str(data)[:500]

    if "error" in data:
        return f"[{tool_name} error] {data['error']}"

    schema = _COMPRESSION_SCHEMA.get(tool_name)
    if schema is None:
        text = json.dumps(data, ensure_ascii=False, default=str)
        return text[:500]

    schema_max = schema.get("max_items")
    if schema_max is None:
        limit = max_items
    else:
        limit = min(schema_max, max_items)

    lines = [f"[{tool_name}]"]
    labels = schema.get("labels") or {}

    # Top-level scalar fields
    for key in schema.get("top_level", []):
        if key in data:
            label = labels.get(key, key)
            lines.append(f"  {label}: {_fmt_val(data[key])}")

    # Nested sections (technical, trend, etc.)
    nested = schema.get("nested")
    if nested:
        lines.extend(_extract_nested(data, nested))

    # List field (screen results, news items, market indices)
    list_field = schema.get("list_field")
    item_fields = schema.get("item_fields", [])
    if list_field and list_field in data:
        items = data[list_field]
        if isinstance(items, list):
            for i, item in enumerate(items[:limit]):
                if not isinstance(item, dict):
                    lines.append(f"  [{i}] {_fmt_val(item)}")
                    continue
                parts = []
                for f in item_fields:
                    if f in item:
                        lbl = labels.get(f, f)
                        parts.append(f"{lbl}={_fmt_val(item[f])}")
                lines.append(f"  [{i}] " + ", ".join(parts))
            if len(items) > limit:
                lines.append(f"  ... (+{len(items) - limit} more)")

    return "\n".join(lines)


def compress_combined(
    outputs: dict[str, dict], *, max_items: int = 10
) -> str:
    """Compress multiple tool outputs into a single combined summary.

    Args:
        outputs: Mapping of tool_name → raw output dict.
        max_items: Max list items per tool.

    Returns:
        Multi-section text separated by blank lines, one section per tool.
    """
    sections = []
    for tool_name, data in outputs.items():
        compressed = compress_output(data, tool_name, max_items=max_items)
        if compressed:
            sections.append(compressed)
    return "\n\n".join(sections)
