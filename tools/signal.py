"""Trade signal submission with validation and audit logging.
Claude Code calls this as a CLI tool via Bash to submit structured trading signals.
Each signal is validated against hard rules before being written to data/signals.jsonl.
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNALS_FILE = os.path.join(ROOT_DIR, "data", "signals.jsonl")
ORDERS_FILE = os.path.join(ROOT_DIR, "data", "orders.json")


def ensure_dir():
    os.makedirs(os.path.dirname(SIGNALS_FILE), exist_ok=True)


def validate_signal(symbol: str, action: str, entry: float, stop: float,
                    target: float, confidence: int, strategy: str,
                    deviation_pct: float | None = None) -> dict:
    """Validate a trade signal against hard rules. Returns {passed, checks, errors}."""
    errors = []
    checks = []

    # 1. Action check
    if action not in ("buy", "sell"):
        errors.append(f"无效操作: {action}，仅支持 buy/sell")
    checks.append({"rule": "action_valid", "passed": action in ("buy", "sell")})

    # 2. Stop loss must be below entry for buy orders
    if action == "buy" and stop >= entry:
        errors.append(f"止损价 {stop} >= 买入价 {entry}，止损必须低于买入价")
    if action == "sell" and stop <= entry:
        errors.append(f"止损价 {stop} <= 卖出价 {entry}")
    checks.append({
        "rule": "stop_loss_valid",
        "passed": (action == "buy" and stop < entry) or (action == "sell" and stop > entry)
    })

    # 3. Take profit reasonable (target > entry for buy)
    if action == "buy" and target <= entry:
        errors.append(f"止盈价 {target} <= 买入价 {entry}")
    checks.append({
        "rule": "target_valid",
        "passed": target > entry if action == "buy" else target < entry
    })

    # 4. Risk-reward ratio check (at least 1.5:1)
    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr_ratio = reward / risk if risk > 0 else 0
    if rr_ratio < 1.5:
        errors.append(f"风险回报比 {rr_ratio:.1f}:1 过低，需 >= 1.5:1")
    checks.append({
        "rule": "risk_reward_ratio",
        "passed": rr_ratio >= 1.5,
        "value": round(rr_ratio, 1),
    })

    # 5. Devation check (乖离率)
    if deviation_pct is not None:
        max_deviation = 7.0 if strategy == "dragon_head" else 5.0
        if deviation_pct > max_deviation:
            errors.append(f"乖离率 {deviation_pct}% > {max_deviation}%，严禁追高")
        checks.append({
            "rule": "deviation_check",
            "passed": deviation_pct <= max_deviation,
            "value": deviation_pct,
            "limit": max_deviation,
        })

    # 6. Confidence range
    if confidence < 0 or confidence > 100:
        errors.append(f"置信度 {confidence} 不在 0-100 范围")
    checks.append({"rule": "confidence_range", "passed": 0 <= confidence <= 100})

    return {
        "passed": len(errors) == 0,
        "checks": checks,
        "errors": errors,
    }


def write_signal(symbol: str, action: str, entry: float, stop: float,
                 target: float, confidence: int, strategy: str,
                 reasoning: str, validation: dict) -> dict:
    """Write a validated signal to the audit log. Returns the signal record."""
    ensure_dir()
    trade_id = f"{datetime.now().strftime('%Y%m%d')}_{symbol}_{uuid.uuid4().hex[:6]}"
    record = {
        "trade_id": trade_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "action": action,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "confidence": confidence,
        "strategy": strategy,
        "reasoning": reasoning,
        "validation": validation,
        "status": "submitted",
        "pnl": None,
    }
    with open(SIGNALS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def submit(args: argparse.Namespace) -> None:
    """Handle the submit subcommand."""
    result = validate_signal(
        symbol=args.symbol,
        action=args.action,
        entry=args.entry,
        stop=args.stop,
        target=args.target,
        confidence=args.confidence,
        strategy=args.strategy,
        deviation_pct=args.deviation,
    )

    output = {
        "valid": result["passed"],
        "checks": result["checks"],
    }

    if not result["passed"]:
        output["error"] = "; ".join(result["errors"])
        output["action"] = "信号被拒绝，请修正后重新提交。"
        print(json.dumps(output, ensure_ascii=False, indent=2))
        sys.exit(0)

    record = write_signal(
        symbol=args.symbol,
        action=args.action,
        entry=args.entry,
        stop=args.stop,
        target=args.target,
        confidence=args.confidence,
        strategy=args.strategy,
        reasoning=args.reasoning,
        validation=result,
    )

    output["trade_id"] = record["trade_id"]
    output["action"] = "信号已通过校验，已写入模拟盘队列。"
    print(json.dumps(output, ensure_ascii=False, indent=2))


def list_signals(args: argparse.Namespace) -> None:
    """Handle the list subcommand."""
    if not os.path.exists(SIGNALS_FILE):
        print(json.dumps({"signals": [], "count": 0}, ensure_ascii=False, indent=2))
        return

    signals = []
    with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                signals.append(json.loads(line))

    limit = args.limit or 20
    recent = signals[-limit:]
    # Return most recent first
    recent.reverse()

    print(json.dumps({
        "signals": recent,
        "count": len(recent),
        "total": len(signals),
    }, ensure_ascii=False, indent=2))


def signal_stats(_args: argparse.Namespace) -> None:
    """Handle the stats subcommand — aggregated signal statistics."""
    if not os.path.exists(SIGNALS_FILE):
        print(json.dumps({"error": "暂无信号记录"}, ensure_ascii=False))
        return

    signals = []
    with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                signals.append(json.loads(line))

    total = len(signals)
    by_action = {"buy": 0, "sell": 0}
    by_strategy = {}
    by_result = {}

    for s in signals:
        by_action[s.get("action", "unknown")] = by_action.get(s.get("action", "unknown"), 0) + 1
        strat = s.get("strategy", "unknown")
        by_strategy[strat] = by_strategy.get(strat, 0) + 1

        pnl = s.get("pnl")
        if pnl is not None:
            status = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
            by_result[status] = by_result.get(status, 0) + 1

    stats = {
        "total_signals": total,
        "by_action": by_action,
        "by_strategy": by_strategy,
    }

    if by_result:
        stats["pnl"] = {
            "win": by_result.get("win", 0),
            "loss": by_result.get("loss", 0),
            "breakeven": by_result.get("breakeven", 0),
            "win_rate": round(by_result.get("win", 0) / len(by_result) * 100, 1) if by_result else None,
        }

    print(json.dumps(stats, ensure_ascii=False, indent=2))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Trade signal submission with validation")
    subparsers = parser.add_subparsers(dest="command", help="Commands: submit, list, stats")

    # submit
    p_submit = subparsers.add_parser("submit", help="Submit a trading signal")
    p_submit.add_argument("--symbol", "-s", required=True, help="Stock code (6 digits)")
    p_submit.add_argument("--action", "-a", required=True, choices=["buy", "sell"],
                          help="Trade direction")
    p_submit.add_argument("--entry", "-e", type=float, required=True, help="Entry price")
    p_submit.add_argument("--stop", "-sl", type=float, required=True, help="Stop loss price")
    p_submit.add_argument("--target", "-tp", type=float, required=True, help="Take profit price")
    p_submit.add_argument("--confidence", "-c", type=int, required=True, help="Confidence 0-100")
    p_submit.add_argument("--strategy", "-st", required=True, help="Strategy name (golden_cross, etc.)")
    p_submit.add_argument("--reasoning", "-r", required=True, help="Reasoning (max 200 chars)")
    p_submit.add_argument("--deviation", "-d", type=float, help="Current deviation % from MA5")

    # list
    p_list = subparsers.add_parser("list", help="List recent signals")
    p_list.add_argument("--limit", "-n", type=int, default=20, help="Number of signals (default: 20)")

    # stats
    subparsers.add_parser("stats", help="Signal statistics")

    args = parser.parse_args()

    if args.command == "submit":
        submit(args)
    elif args.command == "list":
        list_signals(args)
    elif args.command == "stats":
        signal_stats(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
