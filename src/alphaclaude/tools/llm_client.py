"""
LLM client — direct Anthropic SDK calls with Tool Use for guaranteed structured JSON output.

Replaces claude -p subprocess for pipeline stages that produce machine-consumable
structured decisions (direction, candidates, adjustments, emergency).
Sub-agents and Feishu chat still use claude.py → claude -p (full Claude Code context).

Note: anthropic is imported lazily to avoid stdlib signal shadowing by tools/signal.py.
"""

from collections.abc import Callable

from config import ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL, ANTHROPIC_MODEL

_client = None


def _get_client():
    global _client
    if _client is None:
        import sys as _sys
        # tools/signal.py shadows stdlib signal → breaks anthropic → anyio → signal.Signals.
        # Temporarily lift the shadow from both sys.modules and sys.path.
        _shadow_mod = _sys.modules.pop("signal", None)
        _bad_paths = [p for p in _sys.path if "AlphaClaude" in p.replace("\\", "/")]
        for p in _bad_paths:
            _sys.path.remove(p)
        try:
            import anthropic as _anthropic
        finally:
            if _shadow_mod is not None:
                _sys.modules["signal"] = _shadow_mod
            for p in reversed(_bad_paths):
                _sys.path.insert(0, p)
        _client = _anthropic.Anthropic(
            base_url=ANTHROPIC_BASE_URL,
            api_key=ANTHROPIC_AUTH_TOKEN,
            max_retries=1,
            timeout=120,
        )
    return _client


# ── Tool schemas ──────────────────────────────────────────────

TOOL_SET_DIRECTION = {
    "name": "set_direction",
    "description": "设定次日A股交易方向与仓位策略",
    "input_schema": {
        "type": "object",
        "properties": {
            "bias": {
                "type": "string",
                "enum": ["bullish", "neutral", "bearish"],
                "description": "市场方向判断",
            },
            "confidence": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "判断信心度",
            },
            "bias_reasoning": {
                "type": "string",
                "description": "判断理由（含宏观/板块/技术依据）",
            },
            "position_cap": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "总仓位上限百分比",
            },
            "prefer_sectors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "偏好板块列表",
            },
            "avoid_sectors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "回避板块列表",
            },
        },
        "required": ["bias", "confidence", "bias_reasoning", "position_cap"],
    },
}

TOOL_ADD_CANDIDATE = {
    "name": "add_candidate",
    "description": (
        "添加一只买入候选标的。可多次调用添加多只。"
        "止损止盈请用百分比(stop_loss_pct/take_profit_pct)，不要用绝对价。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码"},
            "source": {
                "type": "string",
                "enum": ["B", "C"],
                "description": "B=板块轮动选股, C=自选/复盘推荐",
            },
            "priority": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3,
                "description": "优先级 1=最高 3=最低",
            },
            "entry_max": {"type": "number", "description": "最高买入价"},
            "entry_min": {"type": "number", "description": "最低买入价(回踩买点，可选)"},
            "stop_loss_pct": {"type": "number", "description": "止损百分比(负数，如-5表示-5%)"},
            "take_profit_pct": {"type": "number", "description": "止盈百分比(正数，如8表示+8%)"},
            "stop_loss": {"type": "number", "description": "止损价(不推荐，优先用stop_loss_pct)"},
            "take_profit": {"type": "number", "description": "止盈价(不推荐，优先用take_profit_pct)"},
            "position_pct": {"type": "number", "description": "建议仓位百分比"},
            "cooldown_days": {"type": "integer", "description": "止损后冷却天数(默认1)"},
            "max_hold_days": {"type": "integer", "description": "最长持仓天数(默认5)"},
            "reasoning": {"type": "string", "description": "选股理由"},
        },
        "required": [
            "code", "source", "priority", "entry_max",
            "stop_loss_pct", "take_profit_pct", "position_pct", "reasoning",
        ],
    },
}

TOOL_ADJUST_HOLDING = {
    "name": "adjust_holding",
    "description": "调整现有持仓（上移止损/平仓/持有）。每只持仓调用一次。",
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "股票代码"},
            "action": {
                "type": "string",
                "enum": ["raise_stop", "close", "hold"],
                "description": "raise_stop=上移止损位, close=平仓, hold=继续持有",
            },
            "new_stop_loss": {
                "type": "number",
                "description": "新的止损价（raise_stop 时必填）",
            },
            "reasoning": {"type": "string", "description": "调仓理由"},
        },
        "required": ["code", "action", "reasoning"],
    },
}

TOOL_EMERGENCY_ACTION = {
    "name": "emergency_action",
    "description": "紧急风险响应——减仓/清仓/更新止损",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["hold", "reduce", "close", "close_all"],
                "description": "hold=不动, reduce=减半仓, close=平单只, close_all=全平",
            },
            "code": {
                "type": "string",
                "description": "目标股票代码（close_all 时可为空）",
            },
            "reasoning": {"type": "string", "description": "紧急决策理由"},
            "stop_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "new_stop_loss": {"type": "number"},
                        "reasoning": {"type": "string"},
                    },
                    "required": ["code", "new_stop_loss"],
                },
                "description": "需更新的止损位列表",
            },
        },
        "required": ["action", "reasoning"],
    },
}


# ── Core call function ────────────────────────────────────────

def call_with_tool(
    prompt: str,
    tools: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    tries: int = 2,
) -> list[dict]:
    """Call LLM with tool definitions, return list of tool_use input dicts.

    Each element in the returned list is the ``input`` dict from one tool_use block.
    For single-tool scenarios (direction, emergency), returns a single-element list.
    For multi-tool (candidates, adjustments), returns multiple elements.
    """
    client = _get_client()
    for attempt in range(tries):
        try:
            response = client.messages.create(
                model=model or ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                tools=tools,
                messages=[{"role": "user", "content": prompt}],
            )
            inputs = []
            for block in response.content:
                if block.type == "tool_use":
                    inputs.append(dict(block.input))
            if inputs:
                return inputs
            # No tool call — model output text instead. Retry if possible.
            if attempt < tries - 1:
                prompt = (
                    f"{prompt}\n\n【重要】请调用 {tools[0]['name']} 工具提交结果，"
                    f"不要只输出文本。"
                )
                continue
            return []
        except Exception as exc:
            if attempt < tries - 1:
                continue
            raise exc
    return []


def call_text(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Call LLM without tools, return raw text. For sub-agent summaries when
    Claude Code is not suitable (e.g., short context-free completions)."""
    client = _get_client()
    response = client.messages.create(
        model=model or ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts)


def call_with_tool_safe(
    prompt: str,
    tools: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    tries: int = 2,
    fallback_parser: Callable | None = None,
) -> list[dict]:
    """Call LLM with tool definitions, falling back to text parsing on failure.

    TradingAgents-inspired graceful degradation: try structured output (Tool Use)
    first, then fall back to free-text + parser on any failure. This prevents
    a single API failure from aborting the pre-market plan generation pipeline.

    Args:
        prompt: The user prompt.
        tools: Tool definitions (same as call_with_tool).
        model: Model override.
        max_tokens: Token budget.
        tries: Retry count for the Tool Use attempt.
        fallback_parser: Optional callable(str) -> list[dict] to parse free text
            into structured inputs. If None, returns an empty list on failure.

    Returns:
        List of tool use input dicts (same shape as call_with_tool).
        Never raises — returns [] on complete failure.
    """
    try:
        result = call_with_tool(prompt, tools, model=model, max_tokens=max_tokens, tries=tries)
        if result:
            return result
        # Model returned text instead of tool calls — retries exhausted
        if fallback_parser:
            text = call_text(prompt, model=model, max_tokens=max_tokens)
            return fallback_parser(text)
        return []
    except Exception:
        if fallback_parser is None:
            return []
        try:
            text = call_text(prompt, model=model, max_tokens=max_tokens)
            return fallback_parser(text)
        except Exception:
            return []
