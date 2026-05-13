"""Daily report generator — reads engine output, produces summary.

Usage:
  python -m alphaclaude.tools.daily_report <run_id>                    # latest day
  python -m alphaclaude.tools.daily_report <run_id> --date 2024-03-15  # specific day
  python -m alphaclaude.tools.daily_report <run_id> --all              # all days
  python -m alphaclaude.tools.daily_report <run_id> --push             # push to Feishu
"""
import argparse
import json
import os
from alphaclaude.paths import PROJECT_ROOT
import sys
from datetime import datetime

sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_BASE = os.path.join(
    str(PROJECT_ROOT),
    "data", "output",
)


def load_run_data(run_id: str) -> tuple[dict, list[dict], dict]:
    """Load state.json, ledger.jsonl, plan.json for a run."""
    run_dir = os.path.join(OUTPUT_BASE, run_id)
    if not os.path.isdir(run_dir):
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    state_path = os.path.join(run_dir, "state.json")
    ledger_path = os.path.join(run_dir, "ledger.jsonl")
    plan_path = os.path.join(run_dir, "plan.json")

    state = {}
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)

    ledger = []
    if os.path.exists(ledger_path):
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        ledger.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    plan = {}
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)

    return state, ledger, plan


def compute_stats(state: dict, ledger: list[dict]) -> dict:
    """Compute trading statistics from state and ledger."""
    trades = [e for e in ledger if e.get("decision") in ("open_position", "close_position")]

    total_trades = len(trades)
    winning = sum(1 for t in trades if t.get("pnl", 0) > 0)
    losing = sum(1 for t in trades if t.get("pnl", 0) < 0)
    win_rate = round(winning / max(total_trades, 1) * 100, 1)

    total_pnl = sum(t.get("pnl", 0) for t in trades)
    avg_win = round(sum(
        t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0
    ) / max(winning, 1), 2)
    avg_loss = round(abs(sum(
        t.get("pnl", 0) for t in trades if t.get("pnl", 0) < 0
    )) / max(losing, 1), 2)

    init_capital = state.get("initial_capital", 0)
    current_nav = _latest_nav(state)
    total_return = round(
        (current_nav - init_capital) / init_capital * 100, 2
    ) if init_capital > 0 else 0

    max_nav = init_capital
    max_drawdown = 0
    for entry in state.get("nav_curve", []):
        nav = entry.get("nav", 0)
        max_nav = max(max_nav, nav)
        dd = (max_nav - nav) / max_nav * 100 if max_nav > 0 else 0
        max_drawdown = max(max_drawdown, dd)

    holdings = state.get("holdings", {})
    position_pct = round(
        sum(h["shares"] * h.get("current_price", 0)
            for h in holdings.values()) / max(current_nav, 1) * 100, 1
    )

    return {
        "total_trades": total_trades,
        "winning": winning,
        "losing": losing,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "total_return_pct": total_return,
        "max_drawdown_pct": round(max_drawdown, 2),
        "current_nav": round(current_nav, 2),
        "cash": round(state.get("cash", 0), 2),
        "position_pct": position_pct,
        "holdings": {k: {"shares": v["shares"], "avg_cost": v["avg_cost"],
                          "current_price": v.get("current_price", 0)}
                      for k, v in holdings.items()},
        "total_commission": round(state.get("total_commission", 0), 2),
        "total_stamp_duty": round(state.get("total_stamp_duty", 0), 2),
    }


def _latest_nav(state: dict) -> float:
    curve = state.get("nav_curve", [])
    if curve:
        return curve[-1].get("nav", 0)
    return state.get("initial_capital", 0)


def daily_snapshot(state: dict, ledger: list[dict], date_str: str) -> dict | None:
    """Get stats filtered to a specific date from nav_curve."""
    curve = state.get("nav_curve", [])
    day_entries = [e for e in curve if e.get("time", "").startswith(date_str)]
    if not day_entries:
        return None
    return {
        "date": date_str,
        "nav": day_entries[-1].get("nav"),
        "nav_change": 0,
    }


def generate_report(run_id: str, date_str: str = None, push: bool = False) -> str:
    """Generate and save daily report. Returns JSON string."""
    state, ledger, plan = load_run_data(run_id)
    stats = compute_stats(state, ledger)

    if date_str:
        snapshot = daily_snapshot(state, ledger, date_str)
        title = f"{run_id} — {date_str}"
    else:
        snapshot = None
        title = f"{run_id} — latest"

    report = {
        "run_id": run_id,
        "title": title,
        "generated_at": datetime.now().isoformat(),
        "date": date_str,
        "stats": stats,
        "plan_bias": plan.get("daily_bias", "?"),
        "plan_bias_reason": plan.get("daily_bias_reason", ""),
        "watchlist": plan.get("watchlist", []),
    }

    if snapshot:
        report["snapshot"] = snapshot

    # Save to daily_reports/
    reports_dir = os.path.join(OUTPUT_BASE, run_id, "daily_reports")
    os.makedirs(reports_dir, exist_ok=True)
    fname = f"{date_str}.json" if date_str else f"{datetime.now().strftime('%Y-%m-%d')}.json"
    report_path = os.path.join(reports_dir, fname)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Push to Feishu if requested
    if push:
        _push_to_feishu(report)

    return json.dumps(report, ensure_ascii=False, indent=2)


def _push_to_feishu(report: dict) -> None:
    """Push report as Feishu card message."""
    try:
        from feishu.bot import send_text
        from config import ALERT_CHAT_IDS
    except ImportError:
        print("[daily_report] Feishu SDK not available, skipping push")
        return

    s = report["stats"]
    change = "+" if s["total_return_pct"] > 0 else ""
    msg = (
        f"[AlphaClaude 交易日報] {report['title']}\n"
        f"净值: {s['current_nav']:,.0f} | 收益: {change}{s['total_return_pct']}%\n"
        f"胜率: {s['win_rate']}% | 交易: {s['total_trades']}笔\n"
        f"最大回撤: {s['max_drawdown_pct']}% | 仓位: {s['position_pct']}%\n"
        f"现金: {s['cash']:,.0f} | 盈亏: {s['total_pnl']:,.0f}"
    )

    for cid in ALERT_CHAT_IDS:
        try:
            send_text(cid, msg)
        except Exception:
            pass


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="AlphaClaude Daily Report Generator")
    parser.add_argument("run_id", help="Run ID in data/output/")
    parser.add_argument("--date", "-d", default="",
                        help="Specific date (YYYY-MM-DD), default: latest")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Generate reports for all days")
    parser.add_argument("--push", "-p", action="store_true",
                        help="Push to Feishu (requires ALERT_CHAT_IDS)")

    args = parser.parse_args()

    try:
        if args.all:
            state, ledger, _ = load_run_data(args.run_id)
            dates = set()
            for e in state.get("nav_curve", []):
                d = e.get("time", "")[:10]
                if d:
                    dates.add(d)
            for d in sorted(dates):
                print(f"Generating {d}...")
                generate_report(args.run_id, date_str=d, push=False)
            print(f"Done. {len(dates)} reports saved.")
        else:
            result = generate_report(args.run_id, date_str=args.date or None,
                                     push=args.push)
            print(result)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
