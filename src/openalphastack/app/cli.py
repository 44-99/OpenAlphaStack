"""Unified CLI for OpenAlphaStack."""

from __future__ import annotations

import os
import json
import runpy
import sys

from openalphastack.paths import add_legacy_paths


def run_app() -> None:
    """Run the package application entrypoint."""
    add_legacy_paths()
    from openalphastack.app.main import app
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("OPENALPHASTACK_HOST", "127.0.0.1"),
        port=8800,
        log_level="info",
        timeout_graceful_shutdown=3,
    )


def _print_help() -> None:
    print(
        "usage: openalphastack <command> [<args>]\n\n"
        "commands:\n"
        "  app start                         Start local Dashboard/FastAPI app\n"
        "  mcp serve                         Start the local stdio MCP server\n"
        "  mcp serve-public                  Start the read-only HTTP MCP server\n"
        "  doctor [--json]                   Check local plugin and MCP setup\n"
        "  engine start [engine args]         Start paper/backtest engine\n"
        "  engine list [--mode MODE]          List known engine runs\n"
        "  engine status <run_id>             Show one engine run\n"
        "  engine stop <run_id>               Stop one engine run\n"
        "  engine resume <run_id> --daemon    Resume one engine run\n"
        "  engine stop-running [--mode MODE]  Stop recorded running engines\n"
        "  tools <tool> [tool args]           Run a package tool\n"
    )


def _print_engine_start_help() -> None:
    print(
        "usage: openalphastack engine start [-h] --mode {paper,backtest} [options]\n\n"
        "options:\n"
        "  -h, --help                 show this help message and exit\n"
        "  --mode, -m MODE            paper or backtest\n"
        "  --capital, -c CAPITAL      Initial capital (default: 100000)\n"
        "  --start START              Backtest start date (YYYY-MM-DD)\n"
        "  --end END                  Backtest end date (YYYY-MM-DD)\n"
        "  --universe, -u UNIVERSE    Stock codes, comma-separated, or 'auto'\n"
        "  --watchlist, -w WATCHLIST  Initial watchlist codes\n"
        "  --resume RUN_ID            Resume engine state from run_id\n"
        "  --bar-period MINUTES       Backtest K-line period: 5, 15, 30, or 60\n"
        "  --daemon                   Start detached and return immediately\n"
    )


def _run_engine(args: list[str], prog: str = "openalphastack engine") -> None:
    from openalphastack.engine import cli as engine_cli

    old_argv = sys.argv[:]
    sys.argv = [prog, *args]
    try:
        engine_cli.main()
    finally:
        sys.argv = old_argv


def _run_tool(tool: str, args: list[str]) -> None:
    module_name = f"openalphastack.tools.{tool}"
    old_argv = sys.argv[:]
    sys.argv = [f"openalphastack tools {tool}", *args]
    try:
        runpy.run_module(module_name, run_name="__main__")
    finally:
        sys.argv = old_argv


def main(argv: list[str] | None = None) -> None:
    """Run the unified OpenAlphaStack command router."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        _print_help()
        return

    command = args.pop(0)

    if command == "app":
        if args == ["start"]:
            run_app()
            return
        print("usage: openalphastack app start", file=sys.stderr)
        raise SystemExit(2)

    if command == "mcp":
        if not args or args[0] in {"-h", "--help"}:
            print("usage: openalphastack mcp {serve|serve-public}")
            return
        if args == ["serve"]:
            from openalphastack.mcp_server import run

            run()
            return
        if args == ["serve-public"]:
            from openalphastack.public_mcp_server import run

            run()
            return
        print("usage: openalphastack mcp {serve|serve-public}", file=sys.stderr)
        raise SystemExit(2)

    if command == "doctor":
        if any(arg not in {"--json"} for arg in args):
            print("usage: openalphastack doctor [--json]", file=sys.stderr)
            raise SystemExit(2)
        from openalphastack.doctor import build_report, render_text

        report = build_report()
        print(json.dumps(report, ensure_ascii=False, indent=2) if "--json" in args else render_text(report))
        if not report["ok"]:
            raise SystemExit(1)
        return

    if command == "engine":
        if not args or args[0] in {"-h", "--help"}:
            _print_help()
            return
        action = args.pop(0)
        if action == "start":
            if args and args[0] in {"-h", "--help"}:
                _print_engine_start_help()
                return
            _run_engine(args, prog="openalphastack engine start")
            return
        if action == "list":
            _run_engine(["--list-runs", *args])
            return
        if action == "status":
            if not args:
                print("usage: openalphastack engine status <run_id>", file=sys.stderr)
                raise SystemExit(2)
            run_id = args.pop(0)
            _run_engine(["--status-run", run_id, *args])
            return
        if action == "stop":
            if not args:
                print("usage: openalphastack engine stop <run_id>", file=sys.stderr)
                raise SystemExit(2)
            run_id = args.pop(0)
            _run_engine(["--stop-run", run_id, *args])
            return
        if action == "resume":
            if not args:
                print("usage: openalphastack engine resume <run_id> --daemon", file=sys.stderr)
                raise SystemExit(2)
            run_id = args.pop(0)
            _run_engine(["--resume-run", run_id, *args])
            return
        if action == "stop-running":
            _run_engine(["--stop-running", *args])
            return
        print(f"unknown engine command: {action}", file=sys.stderr)
        raise SystemExit(2)

    if command == "tools":
        if not args:
            print("usage: openalphastack tools <tool> [tool args]", file=sys.stderr)
            raise SystemExit(2)
        tool = args.pop(0)
        _run_tool(tool, args)
        return

    print(f"unknown command: {command}", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
