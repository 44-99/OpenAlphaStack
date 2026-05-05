"""Backtest runner — thin CLI wrapper around paper_engine.py --mode backtest.

Adds convenience: --universe can be a screen strategy name (screen_breakout)
or a comma-separated code list. Runs screen.py to resolve strategy names.

Usage:
  python tools/backtest_runner.py --start 2023-01-01 --end 2024-12-31 --universe default --capital 100000
  python tools/backtest_runner.py --start 2024-06-01 --end 2024-12-31 --universe 600519,000858 --capital 50000
  python tools/backtest_runner.py --resume backtest_2026-05-04T12-00-00
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_engine import PaperEngine


def resolve_universe(spec: str) -> list[str]:
    """Resolve universe specification to list of codes."""
    spec = spec.strip()
    if not spec:
        return []

    # Strategy name: "screen_breakout" or just "breakout"
    strategy = spec
    if spec.startswith("screen_"):
        strategy = spec[7:]

    screen_tool = os.path.join(os.path.dirname(__file__), "screen.py")

    # Check if it's a known strategy
    try:
        result = subprocess.run(
            [sys.executable, screen_tool, "-s", strategy, "--list"],
            capture_output=True, timeout=30,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        if result.returncode == 0:
            # --list just lists strategies; try running the actual screen
            pass
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Run screen.py with the strategy
    try:
        result = subprocess.run(
            [sys.executable, screen_tool, "-s", strategy],
            capture_output=True, timeout=120,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        if result.returncode == 0:
            output = json.loads(result.stdout.decode("utf-8", errors="replace"))
            if isinstance(output, list):
                return [str(r.get("code", "")).zfill(6) for r in output if r.get("code")]
            elif isinstance(output, dict) and "codes" in output:
                return [str(c).zfill(6) for c in output["codes"]]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass

    # Fallback: treat as comma-separated codes
    return [c.strip().zfill(6) for c in spec.split(",") if c.strip()]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="AlphaClaude Backtest Runner — thin wrapper around paper_engine")
    parser.add_argument("--start", "-s", required=True,
                        help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", "-e", required=True,
                        help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--universe", "-u", default="default",
                        help="Stock universe: screen strategy (default/breakout/value/hot_money) "
                             "or comma-separated codes")
    parser.add_argument("--capital", "-c", type=float, default=100000,
                        help="Initial capital (default: 100000)")
    parser.add_argument("--resume", "-r", default="",
                        help="Resume from run_id in data/output/")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without Claude Code (fast lane only)")

    args = parser.parse_args()

    codes = resolve_universe(args.universe)
    if not codes:
        print(f"[BacktestRunner] Error: could not resolve universe: {args.universe}")
        sys.exit(1)

    print(f"[BacktestRunner] Universe: {len(codes)} stocks")
    print(f"[BacktestRunner] Period: {args.start} → {args.end}")
    print(f"[BacktestRunner] Capital: {args.capital:,.0f}")
    if args.dry_run:
        print("[BacktestRunner] Mode: dry-run (fast lane only)")

    engine = PaperEngine(
        mode="backtest",
        capital=args.capital,
        universe=codes,
        backtest_start=args.start,
        backtest_end=args.end,
        resume_run_id=args.resume or None,
        dry_run=args.dry_run,
    )

    try:
        engine.run()
    except KeyboardInterrupt:
        print("\n[BacktestRunner] Interrupted.")
        engine.stop()


if __name__ == "__main__":
    main()
