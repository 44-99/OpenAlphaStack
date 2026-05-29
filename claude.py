"""
Claude Code CLI wrapper — call claude -p for analysis.
"""
import json as _json
import subprocess
import os
import re
from collections.abc import Generator

from config import CLAUDE_CMD, CLAUDE_TIMEOUT

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(
    os.path.expanduser("~"), ".claude", "projects", "E--Project-AlphaClaude"
)


def _session_exists(session_id: str) -> bool:
    return os.path.exists(os.path.join(SESSIONS_DIR, f"{session_id}.jsonl"))


# Token usage from last stream — populated by ask_claude_stream(), read by caller.
_last_token_usage: dict = {}


def get_last_token_usage() -> dict:
    """Return token usage info from the most recent stream-json call.

    Returns dict with keys: input_tokens, output_tokens, cache_creation_input_tokens,
    cache_read_input_tokens. Empty dict if no stream has been run.
    """
    return dict(_last_token_usage)


def ask_claude(prompt: str, session_id: str = None, timeout: int = CLAUDE_TIMEOUT) -> str:
    """Run claude -p. If session_id given, use --resume or --session-id for persistent context."""
    prompt = prompt.strip()
    if not prompt:
        return "抱歉，无法处理空消息。"

    if session_id:
        if _session_exists(session_id):
            cmd = [CLAUDE_CMD, "--resume", session_id, "-p", prompt]
        else:
            cmd = [CLAUDE_CMD, "--session-id", session_id, "-p", prompt]
    else:
        cmd = [CLAUDE_CMD, "-p", prompt]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=PROJECT_DIR,
        )
        output = ""
        if result.stdout:
            output += result.stdout.decode("utf-8", errors="replace")
        if result.stderr:
            stderr_text = result.stderr.decode("utf-8", errors="replace")
            if "Input must be provided" not in stderr_text:
                output += "\n" + stderr_text

        output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)
        return output.strip()

    except subprocess.TimeoutExpired:
        return "分析超时，请稍后再试。"
    except FileNotFoundError:
        return "Claude Code CLI 未找到，请确认已安装。"
    except (OSError, ValueError) as e:
        return f"分析出错: {str(e)}"


def ask_claude_stream(
    prompt: str,
    session_id: str = None,
    timeout: int = CLAUDE_TIMEOUT,
) -> Generator[str, None, None]:
    """Stream Claude Code output token-by-token using --output-format stream-json.

    Yields text chunks as they arrive. Handles session resume/creation
    identically to ask_claude(). Use for real-time Feishu message updates.

    Yields:
        Text chunks from assistant content blocks. Final yield is empty string
        on clean completion. Yields nothing on catastrophic failure.
    """
    prompt = prompt.strip()
    if not prompt:
        yield "抱歉，无法处理空消息。"
        return

    if session_id:
        if _session_exists(session_id):
            cmd = [CLAUDE_CMD, "--resume", session_id, "--output-format", "stream-json", "--verbose", "-p", prompt]
        else:
            cmd = [CLAUDE_CMD, "--session-id", session_id, "--output-format", "stream-json", "--verbose", "-p", prompt]
    else:
        cmd = [CLAUDE_CMD, "--output-format", "stream-json", "--verbose", "-p", prompt]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=PROJECT_DIR,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        yield "Claude Code CLI 未找到，请确认已安装。"
        return
    except (OSError, ValueError) as e:
        yield f"启动分析出错: {str(e)}"
        return

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except (_json.JSONDecodeError, ValueError):
                continue

            msg_type = obj.get("type", "")
            if msg_type == "result":
                break
            if msg_type != "assistant":
                continue

            message = obj.get("message", {})
            # Capture token usage from the assistant message
            usage = message.get("usage")
            if isinstance(usage, dict):
                _last_token_usage.clear()
                _last_token_usage.update(usage)

            content_blocks = message.get("content", [])
            for block in content_blocks:
                if block.get("type") == "text":
                    chunk = block.get("text", "")
                    if chunk:
                        yield chunk

        # Drain stderr after completion
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass
    finally:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def build_trading_prompt(market_data: str, context: str = "") -> str:
    """Build a comprehensive prompt for stock trading analysis."""
    return f"""你是一位资深的A股股票分析师，擅长短线和中线交易策略。

请基于以下实时市场数据，给出专业的分析和推荐：

{market_data}

{context}

请按以下结构回复：

## 一、大盘判断
- 当前市场整体趋势（强势/震荡/弱势）
- 市场情绪和资金面判断
- 今日操作建议仓位（轻仓/半仓/重仓）

## 二、短线推荐（1-5天，3-5支）
对于每支推荐：
- 股票代码和名称
- 推荐理由（技术面/资金面/消息面）
- 建议买入价位
- 止损价和止盈目标价
- 风险提示

## 三、中线推荐（1-4周，2-3支）
对于每支推荐：
- 股票代码和名称
- 推荐逻辑（行业景气/业绩增长/技术形态）
- 建议建仓区间
- 止损位和目标位
- 持仓时间预估

## 四、风险提醒
- 当前主要风险因素
- 需要回避的板块/个股类型
- 仓位管理建议

注意：
1. 每支推荐必须给出具体的买入/止损/止盈价格
2. 推荐要有数据支撑，不可凭空猜测
3. 明确区分短线和中线的不同逻辑
4. 务必提醒风险，不可只讲机会
5. 用中文回复，表达简洁有力
"""
