"""CLI for the OpenAlphaStack engine."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from openalphastack.engine import run_registry
from openalphastack.paths import DATA_DIR, SRC_DIR
from openalphastack.engine.paper import PaperEngine
from openalphastack.engine.universe import fallback_universe, generate_universe
from openalphastack.tools.engine_status import _is_pid_alive

try:
    from openalphastack.tools.notifier import notify_engine_stop
    _notify = True
except Exception:
    _notify = False


def _output_base() -> Path:
    path = DATA_DIR / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _logs_dir() -> Path:
    path = DATA_DIR / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _stop_pid(pid: int) -> bool:
    """Terminate a process by PID. Returns True if it is gone or was signalled."""
    if not _is_pid_alive(pid):
        return False
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False


def stop_running_engines(mode: str | None = None) -> list[dict]:
    """Stop running engine processes recorded in state.json metadata."""
    stopped = []
    for run_dir in _output_base().iterdir():
        if not run_dir.is_dir() or "_" not in run_dir.name:
            continue
        run_mode = run_dir.name.split("_", 1)[0]
        if run_mode not in {"paper", "backtest", "live"}:
            continue
        if mode and run_mode != mode:
            continue
        state = _read_json(run_dir / "state.json")
        meta = state.get("engine_meta", {})
        pid = meta.get("process_id")
        if not pid or not _is_pid_alive(pid):
            continue
        pid_int = int(pid)
        signalled = _stop_pid(pid_int)
        stopped.append({
            "run_id": run_dir.name,
            "mode": run_mode,
            "pid": pid_int,
            "signalled": signalled,
        })
    return stopped


def _daemon_run_id(mode: str) -> str:
    return f"{mode}_{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}"


def _daemon_command(args: argparse.Namespace, run_id: str) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "openalphastack.engine.cli",
        "--mode",
        args.mode,
        "--capital",
        str(args.capital),
        "--resume",
        run_id,
        "--bar-period",
        str(args.bar_period),
    ]
    if args.start:
        cmd.extend(["--start", args.start])
    if args.end:
        cmd.extend(["--end", args.end])
    if args.universe:
        cmd.extend(["--universe", args.universe])
    if args.watchlist:
        cmd.extend(["--watchlist", args.watchlist])
    return cmd


def _existing_run_info(record: run_registry.RunRecord) -> dict:
    """Return daemon-style metadata for an already-alive run."""
    log_prefix = _logs_dir() / record.run_id
    return {
        "pid": record.process_id,
        "run_id": record.run_id,
        "run_dir": record.run_dir,
        "stdout": str(log_prefix.with_suffix(".out.log")),
        "stderr": str(log_prefix.with_suffix(".err.log")),
        "existing": True,
        "status": record.status,
    }


def start_daemon(args: argparse.Namespace) -> dict:
    """Start the engine detached from the current console and return metadata."""
    if args.mode == "paper":
        existing = run_registry.find_active_run("paper", args.resume or None)
        if existing is not None:
            return _existing_run_info(existing)

    run_id = args.resume or _daemon_run_id(args.mode)
    log_prefix = _logs_dir() / run_id
    out_path = log_prefix.with_suffix(".out.log")
    err_path = log_prefix.with_suffix(".err.log")
    env = os.environ.copy()
    src = str(SRC_DIR)
    env["PYTHONPATH"] = src if not env.get("PYTHONPATH") else f"{src}{os.pathsep}{env['PYTHONPATH']}"

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )

    out_f = open(out_path, "ab")
    err_f = open(err_path, "ab")
    try:
        proc = subprocess.Popen(
            _daemon_command(args, run_id),
            cwd=str(Path.cwd()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_f,
            stderr=err_f,
            close_fds=True,
            creationflags=creationflags,
        )
    finally:
        out_f.close()
        err_f.close()

    # Give the child a brief chance to create state.json, but do not wait on it.
    run_dir = _output_base() / run_id
    for _ in range(10):
        if (run_dir / "state.json").exists():
            break
        time.sleep(0.2)

    return {
        "pid": proc.pid,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "stdout": str(out_path),
        "stderr": str(err_path),
    }


def _resume_namespace(plan: run_registry.ResumePlan, base_args: argparse.Namespace | None = None) -> argparse.Namespace:
    args = argparse.Namespace(
        mode=plan.mode,
        capital=100000,
        start=None,
        end=None,
        universe="",
        watchlist="",
        resume=plan.run_id,
        bar_period=60,
    )
    if base_args is not None:
        for name in vars(args):
            if hasattr(base_args, name):
                setattr(args, name, getattr(base_args, name))
    args.mode = plan.mode
    args.resume = plan.run_id
    return args


def resume_run_daemon(run_id: str, base_args: argparse.Namespace | None = None) -> dict:
    """Resume one recorded run as a detached daemon and update metadata."""
    existing = run_registry.find_active_run("paper", run_id)
    if existing is not None:
        info = _existing_run_info(existing)
        info["resume"] = {
            "run_id": existing.run_id,
            "mode": existing.mode,
            "args": [],
            "safe_status": existing.status,
            "resume_count": existing.resume_count,
        }
        return info

    plan = run_registry.build_resume_plan(run_id)
    args = _resume_namespace(plan, base_args)
    info = start_daemon(args)
    if info.get("existing"):
        info["resume"] = plan.to_dict()
        return info
    run_registry.mark_resume_started(plan, int(info["pid"]))
    info["resume"] = plan.to_dict()
    return info


def main() -> None:
    """Run the paper/backtest/live engine."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="OpenAlphaStack deterministic engine — paper/backtest/live")
    parser.add_argument("--mode", "-m",
                        choices=["paper", "backtest", "live"],
                        help="Operating mode")
    parser.add_argument("--capital", "-c", type=float, default=100000,
                        help="Initial capital (default: 100000)")
    parser.add_argument("--start", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--universe", "-u", default="",
                        help="Stock codes (comma-separated) or 'auto' for main board")
    parser.add_argument("--watchlist", "-w", default="",
                        help="Initial watchlist codes")
    parser.add_argument("--resume", help="Resume from run_id")
    parser.add_argument("--bar-period", type=int, default=60,
                        choices=[5, 15, 30, 60],
                        help="K-line period in minutes for backtest (default: 60)")
    parser.add_argument("--daemon", action="store_true",
                        help="Start the engine as a detached background process and return immediately")
    parser.add_argument("--stop-running", action="store_true",
                        help="Stop running paper/backtest/live engines recorded in data/output")
    parser.add_argument("--list-runs", action="store_true",
                        help="List known paper/backtest/live runs from data/output")
    parser.add_argument("--status-run",
                        help="Return one run record by run_id as JSON")
    parser.add_argument("--stop-run",
                        help="Stop one recorded engine run by run_id")
    parser.add_argument("--resume-run",
                        help="Resume one recorded engine run by run_id; requires --daemon")

    args = parser.parse_args()

    if args.list_runs:
        runs = [r.to_dict() for r in run_registry.list_runs(args.mode)]
        print(json.dumps({"runs": runs}, ensure_ascii=False, indent=2))
        return

    if args.status_run:
        try:
            record = run_registry.get_run(args.status_run)
        except run_registry.RunNotFound as e:
            print(json.dumps({"error": str(e), "run_id": e.run_id}, ensure_ascii=False, indent=2))
            raise SystemExit(2)
        print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
        return

    if args.stop_run:
        try:
            result = run_registry.stop_run(args.stop_run)
        except run_registry.RunNotFound as e:
            print(json.dumps({"error": str(e), "run_id": e.run_id}, ensure_ascii=False, indent=2))
            raise SystemExit(2)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    if args.resume_run:
        if not args.daemon:
            parser.error("--resume-run requires --daemon")
        try:
            info = resume_run_daemon(args.resume_run, args)
        except run_registry.RunNotFound as e:
            print(json.dumps({"error": str(e), "run_id": e.run_id}, ensure_ascii=False, indent=2))
            raise SystemExit(2)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    if args.stop_running:
        stopped = stop_running_engines(args.mode)
        if stopped:
            print(json.dumps({"stopped": stopped}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"stopped": []}, ensure_ascii=False, indent=2))
        return

    if not args.mode:
        parser.error("--mode is required unless --stop-running is used")

    if args.daemon:
        info = start_daemon(args)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    if args.universe.lower() == "auto":
        universe = generate_universe()
        print(f"[Main] Auto universe: {len(universe)} stocks")
    elif args.universe:
        universe = [c.strip().zfill(6)
                    for c in args.universe.split(",") if c.strip()]
    else:
        universe = fallback_universe()
        print(f"[Main] Fallback universe: {len(universe)} stocks")

    if args.watchlist:
        for c in args.watchlist.split(","):
            c = c.strip().zfill(6)
            if c and c not in universe:
                universe.append(c)

    engine = PaperEngine(
        mode=args.mode,
        capital=args.capital,
        universe=universe,
        backtest_start=args.start,
        backtest_end=args.end,
        resume_run_id=args.resume,
        bar_period=args.bar_period,
    )

    try:
        if args.mode == "backtest":
            engine.run_backtest()
        else:
            engine.run_paper()
    except KeyboardInterrupt:
        print("\n[Engine] Shutting down...")
        engine.stop()
        if _notify:
            notify_engine_stop(args.mode, "用户中断")
    else:
        if _notify:
            notify_engine_stop(args.mode, "正常退出")


if __name__ == "__main__":
    main()
