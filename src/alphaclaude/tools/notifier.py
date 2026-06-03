"""Engine notification module — pushes key events to Feishu.

All functions are no-ops when ENGINE_CHAT_IDS is empty.
Importable from the package engine or any other engine component.
"""

from alphaclaude.paths import PROJECT_ROOT
import sys
import time
from datetime import datetime

PROJECT_DIR = str(PROJECT_ROOT)
sys.path.insert(0, PROJECT_DIR)

from alphaclaude.config import ENGINE_CHAT_IDS  # noqa: E402
from alphaclaude.feishu.bot import send_post, send_text  # noqa: E402


def _chat_ids():
    return ENGINE_CHAT_IDS


def _send_all(text: str) -> None:
    """Send the same text message to all configured chat IDs."""
    for cid in _chat_ids():
        try:
            send_text(cid, text)
        except Exception:
            pass  # never let notification failure crash the engine


def _send_post_all(title: str, paragraphs: list) -> None:
    """Send a rich post message to all configured chat IDs."""
    for cid in _chat_ids():
        try:
            send_post(cid, title, paragraphs)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# Engine lifecycle
# ═══════════════════════════════════════════════════════════════

def notify_engine_start(
    mode: str,
    capital: float,
    start_date: str = "",
    end_date: str = "",
) -> None:
    """Engine process started."""
    if not _chat_ids():
        return
    mode_label = {"paper": "📝 模拟盘", "backtest": "🔬 回测", "live": "🔴 实盘"}.get(mode, mode)
    now = datetime.now().strftime("%H:%M:%S")
    lines = [
        f"{mode_label}引擎已启动 ({now})",
        f"初始资金: {capital:,.0f} 元",
    ]
    if start_date:
        lines.append(f"区间: {start_date} → {end_date or '至今'}")
    _send_all("\n".join(lines))


def notify_engine_stop(mode: str, reason: str = "") -> None:
    """Engine process stopped normally."""
    if not _chat_ids():
        return
    mode_label = {"paper": "📝 模拟盘", "backtest": "🔬 回测", "live": "🔴 实盘"}.get(mode, mode)
    msg = f"{mode_label}引擎已停止"
    if reason:
        msg += f" ({reason})"
    _send_all(msg)


# ═══════════════════════════════════════════════════════════════
# Daily cycle
# ═══════════════════════════════════════════════════════════════

def notify_overnight_complete(run_id: str, summary: dict) -> None:
    """Pre-market plan generation finished. summary keys: bias, candidates, passed, rejected, nav."""
    if not _chat_ids():
        return
    bias = summary.get("bias", "?")
    candidates = summary.get("candidates", 0)
    passed = summary.get("passed", 0)
    rejected = summary.get("rejected", 0)
    nav = summary.get("nav", 0)
    bias_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(bias, "")
    lines = [
        f"🌅 盘前计划生成完成 {bias_emoji}",
        f"市场研判: {bias} | 候选标的: {candidates} | 通过风控: {passed}/{rejected}拒",
        f"当前净值: {nav:,.0f} 元",
        f"Run: {run_id}",
    ]
    _send_all("\n".join(lines))


def notify_non_trading_premarket(
    run_id: str,
    date: str,
    reason: str,
    nav: float,
    positions_count: int,
) -> None:
    """Pre-market check found a closed market day; no plan or intraday actions."""
    if not _chat_ids():
        return
    lines = [
        "🌅 盘前检查完成：今日不开市",
        f"日期: {date} | 原因: {reason}",
        "处理: 不生成盘前交易计划，不执行盘中买卖动作",
        f"当前净值: {nav:,.0f} 元 | 持仓: {positions_count} 只",
        f"Run: {run_id}",
    ]
    _send_all("\n".join(lines))


def notify_overnight_timeout(run_id: str, stage: str) -> None:
    """Candidate selection or other Claude Code stage timed out."""
    if not _chat_ids():
        return
    msg = f"⚠️ 盘前计划生成超时 — {stage} 阶段未在时限内完成\nRun: {run_id}"
    _send_all(msg)


def notify_sub_agent_summaries(run_id: str, summaries: dict[str, str]) -> None:
    """Send all 3 sub-agent results to Feishu as a rich post message."""
    if not _chat_ids():
        return

    labels = {
        "A": "宏观政策分析",
        "B": "板块轮动分析",
        "C": "交易复盘分析",
    }
    emojis = {"A": "🏛️", "B": "🔄", "C": "📊"}

    for cid in _chat_ids():
        try:
            for key in ["A", "B", "C"]:
                summary_text = summaries.get(key, "")
                if not summary_text:
                    summary_text = "(暂无数据)"
                title = f"{emojis.get(key, '')} 子Agent {key}: {labels.get(key, key)}"
                send_post(cid, title, [summary_text[:2000]])
        except Exception:
            pass


def notify_trading_day_end(
    run_id: str,
    nav: float,
    day_pnl: float,
    day_pnl_pct: float,
    positions: dict,
    trade_count: int = 0,
) -> None:
    """Trading day ended — daily summary."""
    if not _chat_ids():
        return
    pnl_sign = "+" if day_pnl >= 0 else ""
    pnl_emoji = "🟢" if day_pnl >= 0 else "🔴"
    lines = [
        f"{pnl_emoji} 收盘小结",
        f"净值: {nav:,.0f} | 日盈亏: {pnl_sign}{day_pnl:,.0f} ({pnl_sign}{day_pnl_pct:.2f}%)",
        f"持仓: {len(positions)} 只 | 今日成交: {trade_count} 笔",
    ]
    if positions:
        for symbol, h in positions.items():
            unrealized = h.get("unrealized_pnl", 0)
            pnl_s = f"+{unrealized:,.0f}" if unrealized >= 0 else f"{unrealized:,.0f}"
            lines.append(f"  {symbol} {h.get('name','')} — {h.get('shares',0)}股 浮盈亏 {pnl_s}")
    _send_all("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
# Trade notifications
# ═══════════════════════════════════════════════════════════════

# Rule name → Chinese description mapping
_RULE_NAMES_CN = {
    "ma_golden_cross": "MA5/MA10金叉",
    "ma_death_cross": "MA5/MA10死叉",
    "volume_breakout": "放量突破",
    "deviation_alert": "乖离率预警",
    "alignment_turn_bullish": "多头排列转势 (MA5>MA10>MA20)",
    "alignment_turn_bearish": "空头排列转势 (MA5<MA10<MA20)",
    "gap_up": "向上跳空",
    "gap_down": "向下跳空",
    "volume_spike": "成交量异动",
}

_ACTION_LABELS = {
    "buy": "🟢 买入",
    "sell": "🔴 卖出",
    "stop_loss": "🛑 止损",
    "take_profit": "🎯 止盈",
    "emergency": "🚨 紧急平仓",
}

_MODE_ICONS = {"paper": "📝", "backtest": "🔬", "live": "🔴"}


def _format_run_label(run_id: str) -> str:
    """Extract mode icon and time from run_id like 'paper_2026-05-08T22-35-48' → '📝 [PAPER 2026-05-08 22:35:48]'."""
    if not run_id or "_" not in run_id:
        return ""
    mode = run_id.split("_", 1)[0]
    icon = _MODE_ICONS.get(mode, "")
    try:
        parts = run_id.split("_", 1)[1]
        date_part, time_part = parts.split("T", 1)
        time_str = f" {date_part} {time_part.replace('-', ':')}"
    except (ValueError, IndexError):
        time_str = ""
    return f"{icon} [{mode.upper()}{time_str}]"


def _fmt_rule_reason(reason: str) -> str:
    """Translate rule names in reason string to Chinese."""
    for eng, cn in _RULE_NAMES_CN.items():
        if eng in reason:
            reason = reason.replace(eng, f"{cn}({eng})")
            break
    return reason


def notify_trade(
    action: str,
    symbol: str,
    name: str,
    price: float,
    shares: int,
    pnl: float = 0,
    reason: str = "",
    data_time: str = "",
    signal_detail: str = "",
    run_id: str = "",
) -> None:
    """A trade was executed. data_time is the stock data timestamp (not wall clock)."""
    if not _chat_ids():
        return
    action_label = _ACTION_LABELS.get(action, action)
    run_label = _format_run_label(run_id)
    trade_value = price * shares

    prefix = f"{run_label} " if run_label else ""
    lines = [f"{prefix}{action_label} — {symbol} {name}"]
    if data_time:
        lines.append(f"⏰ 数据时间: {data_time}")
    lines.append(f"价格: {price:.2f} | 数量: {shares:,} 股 | 金额: {trade_value:,.0f} 元")

    if action in ("sell", "stop_loss", "take_profit", "emergency") and pnl != 0:
        pnl_s = f"+{pnl:,.0f}" if pnl >= 0 else f"{pnl:,.0f}"
        pnl_pct = pnl / (trade_value - pnl) * 100 if trade_value != pnl else 0
        lines.append(f"盈亏: {pnl_s} 元 ({pnl_pct:+.1f}%)")

    if reason:
        reason_cn = _fmt_rule_reason(reason)
        lines.append(f"策略: {reason_cn[:150]}")

    if signal_detail:
        lines.append(f"指标: {signal_detail[:200]}")

    _send_all("\n".join(lines))


# ═══════════════════════════════════════════════════════════════
# Alerts
# ═══════════════════════════════════════════════════════════════

def notify_alert(level: str, title: str, detail: str = "") -> None:
    """Emergency alert. level: critical / warning / info."""
    if not _chat_ids():
        return
    level_emoji = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(level, "📢")
    msg = f"{level_emoji} [{level.upper()}] {title}"
    if detail:
        msg += f"\n{detail[:300]}"
    _send_all(msg)


# ═══════════════════════════════════════════════════════════════
# Backtest progress
# ═══════════════════════════════════════════════════════════════

_last_progress_time = 0
_PROGRESS_INTERVAL = 600  # min 10 minutes between progress updates


def notify_backtest_progress(
    day_n: int,
    total_days: int,
    nav: float,
    win_rate: float = 0,
    trades: int = 0,
) -> None:
    """Periodic progress update during backtest (rate-limited)."""
    global _last_progress_time
    if not _chat_ids():
        return
    now = time.time()
    if now - _last_progress_time < _PROGRESS_INTERVAL:
        return
    _last_progress_time = now
    pct = day_n / total_days * 100 if total_days > 0 else 0
    msg = (
        f"🔬 回测进度: {day_n}/{total_days} ({pct:.0f}%)\n"
        f"当前净值: {nav:,.0f} | 胜率: {win_rate:.1f}% | 交易: {trades} 笔"
    )
    _send_all(msg)


def notify_backtest_complete(
    nav: float,
    total_return_pct: float,
    win_rate: float,
    sharpe: float,
    max_dd: float,
    trades: int,
) -> None:
    """Backtest finished — final report."""
    if not _chat_ids():
        return
    lines = [
        "✅ 回测完成",
        f"最终净值: {nav:,.0f} | 总收益: {total_return_pct:+.2f}%",
        f"胜率: {win_rate:.1f}% | 夏普: {sharpe:.2f} | 最大回撤: {max_dd:.2f}%",
        f"总交易: {trades} 笔",
    ]
    _send_all("\n".join(lines))
