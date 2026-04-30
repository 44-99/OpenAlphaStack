"""
Claude Code CLI wrapper — call claude -p for analysis.
"""
import subprocess
import os
import re
from config import CLAUDE_CMD, CLAUDE_TIMEOUT

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ask_claude(prompt: str, timeout: int = CLAUDE_TIMEOUT) -> str:
    """Run claude -p and return plain text response."""
    try:
        result = subprocess.run(
            [CLAUDE_CMD, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=PROJECT_DIR,
            encoding="utf-8",
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += "\n" + result.stderr

        # Strip ANSI escape codes
        output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)
        return output.strip()

    except subprocess.TimeoutExpired:
        return "分析超时，请稍后再试。"
    except FileNotFoundError:
        return "Claude Code CLI 未找到，请确认已安装。"
    except Exception as e:
        return f"分析出错: {str(e)}"


def build_trading_prompt(market_data: str, context: str = "") -> str:
    """Build a comprehensive prompt for stock trading analysis."""
    prompt = f"""你是一位资深的A股股票分析师，擅长短线和中线交易策略。

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
    return prompt


def build_chat_prompt(user_message: str, history: list = None) -> str:
    """Build a conversational prompt for group chat / DM."""
    context = ""
    if history:
        recent = history[-10:]  # last 10 exchanges
        for entry in recent:
            role = "用户" if entry["role"] == "user" else "分析师"
            context += f"{role}: {entry['content']}\n"

    return f"""你是飞书群里的A股股票分析助手，帮助用户分析股票和给出交易建议。

Recent conversation:
{context}

用户提问: {user_message}

请基于你的知识给出专业分析。如果问题涉及具体股票，请分析其技术面、基本面和短期走势。
如果问题关于大盘，请给出趋势判断和操作建议。
用中文简洁回复，直接给结论，不要啰嗦。"""
