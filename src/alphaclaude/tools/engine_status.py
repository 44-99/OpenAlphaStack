"""Engine status scanner — reads data/output/ for active engine runs.

Used by the /status Feishu command and potentially by the notifier.
Returns structured status data consumable by both Feishu and web dashboard.
"""

import json
import os
from alphaclaude.paths import PROJECT_ROOT
from datetime import datetime, timedelta

PROJECT_DIR = str(PROJECT_ROOT)
OUTPUT_BASE = os.path.join(PROJECT_DIR, "data", "output")
STALE_HOURS = 2  # runs not updated within this window are considered inactive


def _is_engine_command(line: str) -> bool:
    return (
        "alphaclaude-engine" in line
        or "alphaclaude.engine.cli" in line
        or "alphaclaude\\engine\\cli.py" in line
        or "alphaclaude/engine/cli.py" in line
    )


def _find_engine_modes() -> set[str]:
    """Return the set of modes currently running (paper/backtest/live).

    Parses process command lines to extract --mode <mode>.
    """
    modes = set()
    def _scan_lines(text: str) -> None:
        import re as _re
        for line in text.split("\n"):
            if not _is_engine_command(line):
                continue
            m = _re.search(r"--mode[= ]+(\w+)", line)
            if m:
                modes.add(m.group(1))

    try:
        import subprocess
        # Windows: wmic
        out = subprocess.run(
            ["wmic", "process", "get", "processid,commandline"],
            capture_output=True, text=True, timeout=5,
        )
        _scan_lines(out.stdout)
    except Exception:
        pass
    # Modern Windows: wmic may be unavailable or truncated. PowerShell is more reliable.
    try:
        import subprocess
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine } | "
                "Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True, text=True, timeout=5,
        )
        _scan_lines(out.stdout)
    except Exception:
        pass
    # Unix/Linux: ps aux
    try:
        out = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5,
        )
        _scan_lines(out.stdout)
    except Exception:
        pass
    return modes


def _read_json(path: str) -> dict:
    """Read JSON file, return empty dict on any failure."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _is_pid_alive(pid: int | str | None) -> bool:
    """Best-effort cross-platform PID liveness check without command-line access."""
    if not pid:
        return False
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_int)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid_int, 0)
        return True
    except Exception:
        return False


def _read_ledger_lines(run_dir: str) -> list[dict]:
    """Read ledger.jsonl entries, returning list of parsed dicts."""
    entries = []
    ledger_path = os.path.join(run_dir, "ledger.jsonl")
    try:
        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception:
        pass
    return entries


def _run_summary(run_dir: str, run_id: str, mode: str, is_alive: bool) -> dict:
    """Build a status summary for one engine run."""
    state = _read_json(os.path.join(run_dir, "state.json"))
    plan = _read_json(os.path.join(run_dir, "plan.json"))
    meta = state.get("engine_meta", {})

    # Determine phase
    data_time = state.get("data_time", "")
    plan_updated = plan.get("updated", "")
    phase = "idle"
    if is_alive:
        if meta.get("observation_mode"):
            phase = "observing"
        else:
            now = datetime.now()
            if data_time:
                try:
                    dt = datetime.strptime(data_time[:10], "%Y-%m-%d")
                    if dt.date() == now.date():
                        hour = int(data_time[11:13]) if len(data_time) > 11 else 0
                        if 9 <= hour < 15:
                            phase = "trading"
                        elif 15 <= hour < 16:
                            phase = "post_market"
                        elif hour >= 16 or hour < 9:
                            phase = "plan_ready"
                except ValueError:
                    pass
            else:
                hour = now.hour
                if 9 <= hour < 11 or 13 <= hour < 15:
                    phase = "trading"
                elif 11 <= hour < 13:
                    phase = "lunch_break"
                elif hour >= 15:
                    phase = "post_market"
                else:
                    phase = "pre_market"

            if plan_updated:
                try:
                    upd = datetime.fromisoformat(plan_updated)
                    if upd.date() == now.date() and plan.get("updated_by") != "init":
                        phase = "plan_ready"
                except (ValueError, TypeError):
                    pass
    else:
        if mode == "backtest" and data_time:
            phase = "已完成"
        else:
            phase = "已停止"

    # NAV & P&L
    initial = state.get("initial_capital", 0)
    cash = state.get("cash", 0)
    holdings = state.get("holdings", {})
    nav = _calc_nav(state)

    holdings_value = 0
    unrealized_pnl = 0
    for code, h in holdings.items():
        shares = h.get("shares", 0)
        avg_cost = h.get("avg_cost", 0)
        cur_price = h.get("current_price", avg_cost)
        pos_value = shares * cur_price
        holdings_value += pos_value
        unrealized_pnl += (cur_price - avg_cost) * shares

    total_value = cash + holdings_value
    total_pnl = total_value - initial
    total_pnl_pct = total_pnl / initial * 100 if initial > 0 else 0

    # Enrich holdings with position_pct
    for code in holdings:
        h = holdings[code]
        pos_val = h["shares"] * h.get("current_price", h.get("avg_cost", 0))
        holdings[code]["position_pct"] = (pos_val / total_value * 100) if total_value > 0 else 0

    # Day P&L from nav_curve: last two distinct-day entries
    nav_curve = state.get("nav_curve", [])
    day_pnl = 0.0
    day_pnl_pct = 0.0
    if len(nav_curve) >= 2:
        # Group by date, take last NAV per day
        daily_navs = {}
        for n in nav_curve:
            t = n.get("time", "")
            if t:
                date_key = t[:10]  # YYYY-MM-DD
                daily_navs[date_key] = n["nav"]
        sorted_navs = sorted(daily_navs.items())
        if len(sorted_navs) >= 2:
            prev_nav = sorted_navs[-2][1]
            day_pnl = total_value - prev_nav
            day_pnl_pct = day_pnl / prev_nav * 100 if prev_nav > 0 else 0

    # Max drawdown from nav_curve
    max_dd = 0.0
    if nav_curve:
        all_navs = [n["nav"] for n in nav_curve]
        peak = all_navs[0]
        for v in all_navs:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

    # Today's trades from ledger (paper/live only — trade_id uses real date)
    today_str = datetime.now().strftime("%Y%m%d")
    today_trades = 0
    ledger_entries = _read_ledger_lines(run_dir)
    for e in ledger_entries:
        if e.get("status") != "executed":
            continue
        if e.get("decision") not in ("open_position", "close_position"):
            continue
        if e.get("trade_id", "")[:8] == today_str:
            today_trades += 1

    # Trades
    trade_count = state.get("trade_count", 0)
    win_count = state.get("win_count", 0)
    win_rate = win_count / trade_count * 100 if trade_count > 0 else 0

    # Fees
    total_commission = state.get("total_commission", 0)
    total_stamp_duty = state.get("total_stamp_duty", 0)

    # Plan info
    market_bias = plan.get("market_bias", "?")
    bias_confidence = plan.get("bias_confidence", 0)
    candidates = plan.get("buy_candidates", [])
    pending_orders = plan.get("pending_orders", [])
    watchlist = plan.get("watchlist", [])
    adjustments = plan.get("holding_adjustments", [])
    cooldown = plan.get("cooldown", {})
    today_stopped_out = plan.get("today_stopped_out", [])
    rules = plan.get("rules", {})

    return {
        "run_id": run_id,
        "mode": mode,
        "phase": phase,
        "is_alive": is_alive,
        "data_time": data_time,
        "plan_updated": plan_updated,
        "initial_capital": initial,
        "cash": cash,
        "nav": nav,
        "total_value": total_value,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "holdings_value": holdings_value,
        "unrealized_pnl": unrealized_pnl,
        "holdings": holdings,
        "trade_count": trade_count,
        "win_count": win_count,
        "win_rate": win_rate,
        "market_bias": market_bias,
        "bias_confidence": bias_confidence,
        "candidates_count": len(candidates),
        "pending_orders_count": len(pending_orders),
        "watchlist_count": len(watchlist),
        "adjustments_count": len(adjustments),
        # New fields
        "engine_meta": meta,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "max_drawdown": max_dd,
        "today_trades": today_trades,
        "total_commission": total_commission,
        "total_stamp_duty": total_stamp_duty,
        "cooldown_count": len(cooldown),
        "cooldown_codes": list(cooldown.keys())[:10],
        "stopped_out_count": len(today_stopped_out),
        "stopped_out_codes": today_stopped_out[:10],
        "rules": rules,
    }


def _calc_nav(state: dict) -> float:
    """Calculate current NAV from state."""
    cash = state.get("cash", 0)
    frozen = state.get("frozen_cash", 0)
    holdings = state.get("holdings", {})
    holdings_value = 0
    for h in holdings.values():
        shares = h.get("shares", 0)
        price = h.get("current_price", h.get("avg_cost", 0))
        holdings_value += shares * price
    return cash + frozen + holdings_value


def get_all_runs() -> list[dict]:
    """Scan data/output/ and return status for all runs.

    Returns list sorted by recency, most recent first.
    - "Alive" runs: the most recent per mode when an engine process exists.
    - "Recent" runs: updated within STALE_HOURS but not the active one.
    """
    if not os.path.exists(OUTPUT_BASE):
        return []

    running_modes = _find_engine_modes()  # e.g. {"paper"} or {"paper", "backtest"}
    results = []
    now = datetime.now()
    recent_cutoff = now - timedelta(hours=STALE_HOURS)

    # Determine most recent run ID per mode
    mode_latest: dict[str, str] = {}
    for entry in sorted(os.listdir(OUTPUT_BASE)):
        run_dir = os.path.join(OUTPUT_BASE, entry)
        if not os.path.isdir(run_dir):
            continue
        if not os.path.exists(os.path.join(run_dir, "state.json")):
            continue
        mode = entry.split("_")[0] if "_" in entry else ""
        if mode in ("paper", "backtest", "live"):
            mode_latest[mode] = entry  # last (most recent) wins

    for entry in sorted(os.listdir(OUTPUT_BASE), reverse=True):
        run_dir = os.path.join(OUTPUT_BASE, entry)
        if not os.path.isdir(run_dir):
            continue
        state_path = os.path.join(run_dir, "state.json")
        if not os.path.exists(state_path):
            continue

        mode = entry.split("_")[0] if "_" in entry else "unknown"
        if mode not in ("paper", "backtest", "live"):
            continue

        run_mtime = os.path.getmtime(state_path)
        run_dt = datetime.fromtimestamp(run_mtime)

        # Quick check on state to decide filtering
        state = _read_json(state_path)
        meta = state.get("engine_meta", {})

        # Alive = exact PID from engine metadata, or fallback to mode-level process detection.
        is_alive = _is_pid_alive(meta.get("process_id")) or (
            (mode in running_modes) and (mode_latest.get(mode) == entry)
        )

        has_holdings = len(state.get("holdings", {})) > 0
        has_trades = state.get("trade_count", 0) > 0
        is_interesting = has_holdings or has_trades

        # Filter: always show alive + interesting runs; only show stale empty runs if recent
        if not is_alive and not is_interesting and run_dt < recent_cutoff:
            continue

        summary = _run_summary(run_dir, entry, mode, is_alive)
        results.append(summary)

    return results


def get_active_run() -> dict | None:
    """Get the single most-recent active run, or None."""
    runs = get_all_runs()
    for r in runs:
        if r["is_alive"]:
            return r
    return runs[0] if runs else None


def get_alive_count() -> dict:
    """Quick count of alive runs by mode."""
    runs = get_all_runs()
    alive = [r for r in runs if r["is_alive"]]
    return {
        "total": len(runs),
        "alive": len(alive),
        "stopped": len(runs) - len(alive),
        "modes": {
            "paper": len([r for r in alive if r["mode"] == "paper"]),
            "backtest": len([r for r in alive if r["mode"] == "backtest"]),
            "live": len([r for r in alive if r["mode"] == "live"]),
        },
    }


def format_status_text(runs: list[dict] | None = None) -> str:
    """Format engine status as a Feishu-friendly text message.

    Shows at most: all alive runs + the most interesting recent stopped runs
    (those with holdings or trades). Cap total displayed runs at 6.
    """
    if runs is None:
        runs = get_all_runs()

    if not runs:
        return "当前没有活跃或最近的引擎实例。\n\n启动：alphaclaude-engine --mode paper --capital 100000"

    # Prioritize: alive runs first, then recent runs with holdings/trades
    alive = [r for r in runs if r["is_alive"]]
    recent_with_holdings = [r for r in runs if not r["is_alive"] and len(r.get("holdings", {})) > 0]
    recent_with_trades = [r for r in runs if not r["is_alive"]
                          and len(r.get("holdings", {})) == 0
                          and r["trade_count"] > 0]
    other_recent = [r for r in runs
                    if not r["is_alive"] and r not in recent_with_holdings and r not in recent_with_trades]

    display = alive + recent_with_holdings + recent_with_trades
    # Fill remaining slots with most recent others, up to 6 total
    for r in other_recent:
        if len(display) >= 6:
            break
        display.append(r)

    lines = []
    now_str = datetime.now().strftime("%H:%M:%S")
    lines.append(f"引擎状态 ({now_str})")
    if alive:
        modes = ", ".join(f"{r['mode']}" for r in alive)
        lines.append(f"🟢 运行中: {len(alive)} 个 ({modes}) | 最近已停止: {len(runs) - len(alive)} 个")
    else:
        lines.append(f"⚫ 所有引擎已停止，最近 {len(display)} 个实例：")
    lines.append("")

    for r in display:
        icon = {"paper": "📝", "backtest": "🔬", "live": "🔴"}.get(r["mode"], "❓")
        status = "🟢" if r["is_alive"] else "⚫"

        # Extract time suffix from run_id (e.g. "paper_2026-05-06T16-06-33" → "2026-05-06 16:06:33")
        rid = r["run_id"]
        time_suffix = ""
        if "_" in rid and "T" in rid:
            try:
                parts = rid.split("_", 1)[1]
                date_part, time_part = parts.split("T", 1)
                time_suffix = f" {date_part} {time_part.replace('-', ':')}"
            except (ValueError, IndexError):
                pass

        phase_label = {
            "pre_market": "盘前等待",
            "trading": "盘中交易",
            "lunch_break": "午间休市",
            "post_market": "盘后处理",
            "plan_ready": "盘前计划已生成",
            "observing": "观察模式",
            "stopped": "已停止",
            "idle": "空闲",
        }.get(r["phase"], r["phase"])

        lines.append(f"{icon} {status} [{r['mode'].upper()}{time_suffix}] {phase_label}")

        # ── Backtest-specific: range, universe, progress ──
        meta = r.get("engine_meta", {})
        if r["mode"] == "backtest":
            bt_start = meta.get("backtest_start", "")
            bt_end = meta.get("backtest_end", "")
            if bt_start or bt_end:
                lines.append(f"回测区间: {bt_start} → {bt_end}")
            uni_size = meta.get("universe_size", 0)
            if uni_size:
                lines.append(f"股票范围: {uni_size:,} 只")
            progress = meta.get("progress", {})
            cur = progress.get("current_day", 0)
            tot = progress.get("total_days", 0)
            claude = meta.get("claude_every", 0)
            if tot > 0:
                pct = cur / tot * 100
                prog_line = f"进度: {cur}/{tot} ({pct:.0f}%)"
                if claude:
                    prog_line += f" | Claude 每 {claude} 天"
                lines.append(prog_line)

        # ── Financial summary ──
        pnl_sign = "+" if r["total_pnl"] >= 0 else ""
        pct_sign = "+" if r["total_pnl_pct"] >= 0 else ""
        lines.append(
            f"净值 {r['total_value']:,.0f} | "
            f"盈亏 {pnl_sign}{r['total_pnl']:,.0f} ({pct_sign}{r['total_pnl_pct']:.2f}%) | "
            f"现金 {r['cash']:,.0f}"
        )

        # ── Day P&L (paper/live only, backtest data_time is historical) ──
        if r["mode"] != "backtest":
            dp_sign = "+" if r["day_pnl"] >= 0 else ""
            dp_pct_sign = "+" if r["day_pnl_pct"] >= 0 else ""
            if r["day_pnl"] != 0:
                lines.append(f"今日盈亏: {dp_sign}{r['day_pnl']:,.0f} ({dp_pct_sign}{r['day_pnl_pct']:.2f}%)")

        # ── Max drawdown ──
        if r["max_drawdown"] > 0:
            lines.append(f"最大回撤: -{r['max_drawdown']:.2f}%")

        # ── Holdings ──
        n_holdings = len(r["holdings"])
        if n_holdings > 0:
            trade_line = f"持仓 {n_holdings} 只 | 累计交易 {r['trade_count']} 笔"
            if r["mode"] != "backtest" and r["today_trades"] > 0:
                trade_line += f" | 今日成交 {r['today_trades']} 笔"
            trade_line += f" | 胜率 {r['win_rate']:.0f}%"
            lines.append(trade_line)
            for code, h in list(r["holdings"].items())[:8]:
                shares = h.get("shares", 0)
                avg = h.get("avg_cost", 0)
                cur = h.get("current_price", avg)
                pnl = (cur - avg) * shares
                pnl_pct = (cur - avg) / avg * 100 if avg > 0 else 0
                pnl_s = f"+{pnl:,.0f}" if pnl >= 0 else f"{pnl:,.0f}"
                pos_pct = h.get("position_pct", 0)
                lines.append(f"  {code} {shares}股 @{avg:.2f}→{cur:.2f} {pnl_s} ({pnl_pct:+.1f}%) 占净值{pos_pct:.0f}%")
        else:
            trade_line = f"持仓 0 | 累计交易 {r['trade_count']} 笔"
            if r["mode"] != "backtest" and r["today_trades"] > 0:
                trade_line += f" | 今日成交 {r['today_trades']} 笔"
            lines.append(trade_line)

        # ── Fees ──
        commission = r.get("total_commission", 0)
        duty = r.get("total_stamp_duty", 0)
        if commission > 0 or duty > 0:
            lines.append(f"费用: 佣金 {commission:,.0f} | 印花税 {duty:,.0f}")

        # ── Market bias & plan ──
        bias_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(r["market_bias"], "")
        plan_parts = []
        if r["market_bias"] != "neutral" or r["candidates_count"] > 0:
            plan_parts.append(f"研判 {bias_emoji} {r['market_bias']} ({r['bias_confidence']}%)")
        if r["candidates_count"] > 0:
            plan_parts.append(f"候选 {r['candidates_count']}")
        if r["pending_orders_count"] > 0:
            plan_parts.append(f"待执行 {r['pending_orders_count']}")
        if plan_parts:
            lines.append(" | ".join(plan_parts))

        # ── Risk / cooldown / stopped out ──
        rules = r.get("rules", {})
        risk_parts = []
        if rules:
            max_single = rules.get("max_single_position_pct", 0)
            max_total = rules.get("max_total_position_pct", 0)
            if max_single and max_total:
                risk_parts.append(f"仓位上限: 单股{max_single:.0f}% 总{max_total:.0f}%")
        if r["cooldown_count"] > 0:
            codes = ", ".join(r.get("cooldown_codes", [])[:5])
            risk_parts.append(f"冷却: {codes}")
        if r["stopped_out_count"] > 0:
            codes = ", ".join(r.get("stopped_out_codes", [])[:5])
            risk_parts.append(f"今日止损: {codes}")
        if risk_parts:
            lines.append(" | ".join(risk_parts))

        if r["data_time"]:
            lines.append(f"数据时间: {r['data_time']}")
        lines.append("")

    if alive:
        lines.append("—" * 20)
        lines.append("/status 刷新 | /positions 持仓 | /stop 停止")

    return "\n".join(lines)


def format_positions_text() -> str:
    """Format detailed holdings for the active engine run."""
    runs = get_all_runs()
    alive = [r for r in runs if r["is_alive"] and r["holdings"]]
    recent = [r for r in runs if not r["is_alive"] and r["holdings"]]

    if not alive and not recent:
        return "当前无持仓。"

    lines = ["📋 持仓详情"]
    for r in alive + recent[:1]:
        icon = {"paper": "📝", "backtest": "🔬", "live": "🔴"}.get(r["mode"], "❓")
        status = "🟢" if r["is_alive"] else "⚫"
        pnl_sign = "+" if r["total_pnl"] >= 0 else ""
        pct_sign = "+" if r["total_pnl_pct"] >= 0 else ""

        # Extract short time suffix
        rid = r["run_id"]
        time_suffix = ""
        if "_" in rid and "T" in rid:
            try:
                parts = rid.split("_", 1)[1]
                date_part, time_part = parts.split("T", 1)
                time_suffix = f" {date_part[5:]} {time_part.replace('-', ':')[:5]}"
            except (ValueError, IndexError):
                pass

        lines.append(f"\n{icon} {status} [{r['mode'].upper()}{time_suffix}] "
                     f"净值 {r['total_value']:,.0f} | "
                     f"总盈亏 {pnl_sign}{r['total_pnl']:,.0f} ({pct_sign}{r['total_pnl_pct']:.2f}%)")

        for code, h in r["holdings"].items():
            shares = h.get("shares", 0)
            avg = h.get("avg_cost", 0)
            cur = h.get("current_price", avg)
            strategy = h.get("strategy", "")
            sl = h.get("stop_loss", 0)
            tp = h.get("take_profit", 0)
            pnl = (cur - avg) * shares
            pnl_pct = (cur - avg) / avg * 100 if avg > 0 else 0
            pnl_s = f"+{pnl:,.0f}" if pnl >= 0 else f"{pnl:,.0f}"

            lines.append(f"  {code} {shares}股")
            pos_pct = h.get("position_pct", 0)
            lines.append(f"    成本 {avg:.2f} → 现价 {cur:.2f}  浮盈亏 {pnl_s} ({pnl_pct:+.1f}%)  占净值{pos_pct:.0f}%")
            if strategy:
                lines.append(f"    策略: {strategy}")
            if sl:
                lines.append(f"    止损 {sl:.2f}  止盈 {tp:.2f}")

    return "\n".join(lines)


def stop_engine() -> str:
    """Kill AlphaClaude engine processes. Returns status message."""
    import signal as _signal
    import subprocess

    # Find PIDs of engine processes
    pids = set()
    try:
        out = subprocess.run(
            ["wmic", "process", "get", "processid,commandline"],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.split("\n"):
            if _is_engine_command(line):
                nums = [int(s) for s in line.split() if s.isdigit()]
                pids.update(nums)
    except Exception:
        pass

    if not pids:
        return "没有正在运行的引擎进程。"

    killed = []
    failed = []
    for pid in pids:
        try:
            os.kill(pid, _signal.SIGTERM)
            killed.append(pid)
        except Exception:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, timeout=5)
                killed.append(pid)
            except Exception:
                failed.append(pid)

    lines = ["⏹️ 引擎已停止"]
    if killed:
        lines.append(f"已终止进程: {', '.join(str(p) for p in killed)}")
    if failed:
        lines.append(f"无法终止: {', '.join(str(p) for p in failed)}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI entry point (for testing)
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    runs = get_all_runs()
    print(format_status_text(runs))
