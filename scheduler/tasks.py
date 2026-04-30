"""
Scheduled tasks — daily stock analysis and messaging.
"""
from datetime import datetime
from feishu.bot import send_text
from stock.data import format_market_report
from claude.client import ask_claude, build_trading_prompt
from config import TARGET_CHAT_IDS

# Chat IDs will be populated from webhook events
_subscribers: list[str] = list(TARGET_CHAT_IDS)


def update_subscribers(chat_ids: list[str]) -> None:
    """Update the list of chat IDs to send reports to."""
    global _subscribers
    _subscribers = list(set(chat_ids))
    # Persist to config
    import config
    config.TARGET_CHAT_IDS = _subscribers


def run_morning_analysis() -> None:
    """9:00 AM — Morning briefing with market overview and recommendations."""
    print(f"[{datetime.now()}] 执行早盘分析...")
    try:
        market_data = format_market_report()
        prompt = build_trading_prompt(
            market_data,
            context="现在是早盘开盘前，请重点给出今日的操作策略和可以关注的标的。"
        )
        analysis = ask_claude(prompt, timeout=180)

        msg = f"☀️ 早盘简报\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{analysis}"
        for chat_id in _subscribers:
            send_text(chat_id, msg)
            print(f"  已发送到 {chat_id}")
    except Exception as e:
        print(f"  早盘分析失败: {e}")


def run_midday_update() -> None:
    """12:00 PM — Midday market update."""
    print(f"[{datetime.now()}] 执行午间更新...")
    try:
        market_data = format_market_report()
        prompt = build_trading_prompt(
            market_data,
            context="现在是午间休盘，请重点总结上午走势，给出下午的操作建议和可以关注的标的。"
        )
        analysis = ask_claude(prompt, timeout=180)

        msg = f"🌤 午间速报\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{analysis}"
        for chat_id in _subscribers:
            send_text(chat_id, msg)
            print(f"  已发送到 {chat_id}")
    except Exception as e:
        print(f"  午间更新失败: {e}")


def run_closing_summary() -> None:
    """3:30 PM — Closing summary and next-day outlook."""
    print(f"[{datetime.now()}] 执行收盘总结...")
    try:
        market_data = format_market_report()
        prompt = build_trading_prompt(
            market_data,
            context="现在是收盘后，请做全天复盘总结，给出明日展望和可以提前关注的标的。"
        )
        analysis = ask_claude(prompt, timeout=180)

        msg = f"🌙 收盘总结\n{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{analysis}"
        for chat_id in _subscribers:
            send_text(chat_id, msg)
            print(f"  已发送到 {chat_id}")
    except Exception as e:
        print(f"  收盘总结失败: {e}")
