"""Shadow Account — behavioral diagnostics from ledger.jsonl.

Pure Python computes the numbers; Claude Code interprets the patterns.
"""
import argparse
import json
import os
from alphaclaude.paths import PROJECT_ROOT
import sys
from collections import defaultdict, deque
from datetime import datetime

PROJECT_DIR = str(PROJECT_ROOT)


def load_ledger(run_id: str) -> list[dict]:
    """Read ledger.jsonl for a given run_id."""
    path = os.path.join(PROJECT_DIR, "data", "output", run_id, "ledger.jsonl")
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _entry_date(entry: dict, entries: list[dict], idx: int) -> str | None:
    """Best-effort date from entry. Ledger has time=HH:MM:SS, no date field.
    Uses the entry's own 'date' field if present, otherwise infers from context."""
    t = entry.get("time", "")
    if "T" in t:
        return t[:10]
    return entry.get("date", "")


def pair_trades(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Internal: generate completed pairs from entries."""
    opens: dict[str, deque] = defaultdict(deque)
    ref_date = _find_base_date(entries)
    completed = []

    for entry in entries:
        d = entry.get("decision", "")
        if d == "open_position":
            code = entry.get("symbol", "")
            opens[code].append({
                "symbol": code,
                "entry_price": entry.get("price", 0),
                "entry_time": entry.get("time", ""),
                "entry_date": entry.get("date", ref_date),
                "strategy": entry.get("strategy", ""),
                "entry_reasoning": entry.get("reasoning", ""),
                "remaining_shares": entry.get("shares", 0),
            })
        elif d == "close_position":
            code = entry.get("symbol", "")
            close_shares = entry.get("shares", 0)
            close_price = entry.get("price", 0)
            close_pnl = entry.get("pnl", 0)
            close_pnl_pct = entry.get("pnl_pct", 0)
            remaining = close_shares
            while remaining > 0 and opens.get(code):
                lot = opens[code][0]
                matched = min(remaining, lot["remaining_shares"])
                pnl_share = close_pnl * (matched / close_shares) if close_shares else 0
                remaining -= matched
                lot["remaining_shares"] -= matched
                if lot["remaining_shares"] <= 0:
                    opens[code].popleft()
                completed.append({
                    "symbol": code,
                    "entry_price": lot["entry_price"],
                    "entry_time": lot["entry_time"],
                    "entry_date": lot["entry_date"],
                    "exit_price": close_price,
                    "exit_time": entry.get("time", ""),
                    "exit_date": entry.get("date", ref_date),
                    "shares": matched,
                    "pnl": round(pnl_share, 2),
                    "pnl_pct": close_pnl_pct,
                    "strategy": lot["strategy"],
                    "entry_reasoning": lot["entry_reasoning"],
                    "exit_reasoning": entry.get("reasoning", ""),
                })

    open_list = []
    for code, lots in opens.items():
        for lot in lots:
            if lot["remaining_shares"] > 0:
                open_list.append(dict(lot))

    return completed, open_list


def _find_base_date(entries: list[dict]) -> str:
    """Find a reference date from ledger entries."""
    for e in entries:
        for key in ("date", "data_time"):
            if key in e and e[key]:
                return str(e[key])[:10]
    for e in entries:
        t = e.get("time", "")
        if "T" in t:
            return t[:10]
    return datetime.now().strftime("%Y-%m-%d")


def _holding_days(pair: dict) -> float:
    """Estimate holding days from entry/exit date strings."""
    entry = pair.get("entry_date", "")
    exit_d = pair.get("exit_date", "")
    if entry and exit_d:
        try:
            d1 = datetime.strptime(str(entry)[:10], "%Y-%m-%d")
            d2 = datetime.strptime(str(exit_d)[:10], "%Y-%m-%d")
            return max((d2 - d1).days, 1)
        except ValueError:
            pass
    return 1


def compute_diagnostics(paired: list[dict], open_positions: list[dict],
                        entries: list[dict]) -> dict:
    """Compute all behavioral metrics from paired trades."""
    if not paired:
        return {"trade_count": len(open_positions), "paired_trades_count": 0,
                "open_positions_count": len(open_positions), "summary": {},
                "behavioral_diagnostics": {}, "strategy_pnl_breakdown": {},
                "pnl_by_weekday": {}, "recurring_patterns": []}

    winners = [p for p in paired if p.get("pnl", 0) > 0]
    losers = [p for p in paired if p.get("pnl", 0) <= 0]

    # Holding durations
    winner_days = [_holding_days(p) for p in winners]
    loser_days = [_holding_days(p) for p in losers]
    avg_winner_days = sum(winner_days) / len(winner_days) if winner_days else 0
    avg_loser_days = sum(loser_days) / len(loser_days) if loser_days else 0
    disp_ratio = round(avg_loser_days / avg_winner_days, 2) if avg_winner_days > 0 else 0

    # P&L stats
    winner_pnl_pcts = [p.get("pnl_pct", 0) for p in winners]
    loser_pnl_pcts = [p.get("pnl_pct", 0) for p in losers]
    avg_winner_pnl = round(sum(winner_pnl_pcts) / len(winner_pnl_pcts), 2) if winner_pnl_pcts else 0
    avg_loser_pnl = round(sum(loser_pnl_pcts) / len(loser_pnl_pcts), 2) if loser_pnl_pcts else 0

    # Strategy breakdown
    strat_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0.0})
    for p in paired:
        s = p.get("strategy", "unknown")
        strat_stats[s]["trades"] += 1
        if p.get("pnl", 0) > 0:
            strat_stats[s]["wins"] += 1
        strat_stats[s]["total_pnl"] += p.get("pnl", 0)

    strategy_breakdown = {}
    for s, st in strat_stats.items():
        strategy_breakdown[s] = {
            "trades": st["trades"],
            "win_rate": round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0,
            "total_pnl": round(st["total_pnl"], 2),
        }

    # Weekday breakdown
    weekday_pnl: dict[str, float] = defaultdict(float)
    for p in paired:
        d = p.get("entry_date", "")
        if d:
            try:
                dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
                wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()]
                weekday_pnl[wd] += p.get("pnl", 0)
            except ValueError:
                pass

    # Overtrading: trade count per day from open_position entries
    daily_trade_count: dict[str, int] = defaultdict(int)
    for e in entries:
        if e.get("decision") == "open_position":
            d = e.get("date", _find_base_date([e]))
            if d:
                daily_trade_count[str(d)[:10]] += 1
    max_trades_per_day = max(daily_trade_count.values()) if daily_trade_count else 0
    avg_trades_per_day = round(sum(daily_trade_count.values()) / len(daily_trade_count), 1) if daily_trade_count else 0

    # Bearish-market entries: check overnight_bias before open_position
    bearish_entries = 0
    bearish_loss = 0.0
    last_bias = "neutral"
    for e in entries:
        if e.get("decision") == "overnight_bias":
            last_bias = e.get("value", "neutral")
        elif e.get("decision") == "open_position" and last_bias == "bearish":
            bearish_entries += 1
        elif e.get("decision") == "close_position" and last_bias == "bearish":
            bearish_loss += e.get("pnl", 0)

    # Strategy deviation: exit reason contains keywords suggesting plan abandonment
    deviation_keywords = ["止损", "恐慌", "紧急", "回调", "扛不住"]
    deviations = 0
    for p in paired:
        exit_r = p.get("exit_reasoning", "")
        if any(kw in exit_r for kw in deviation_keywords):
            deviations += 1
    deviation_rate = round(deviations / len(paired), 2) if paired else 0

    # Detect recurring patterns
    recurring = _detect_patterns(paired, disp_ratio, bearish_entries, bearish_loss,
                                  deviations, max_trades_per_day, avg_loser_pnl,
                                  winner_pnl_pcts, loser_pnl_pcts, strategy_breakdown)

    return {
        "trade_count": len(paired) + len(open_positions),
        "paired_trades_count": len(paired),
        "open_positions_count": len(open_positions),
        "summary": {
            "win_rate": round(len(winners) / len(paired) * 100, 1) if paired else 0,
            "total_pnl": round(sum(p.get("pnl", 0) for p in paired), 2),
            "avg_winner_hold_days": round(avg_winner_days, 1),
            "avg_loser_hold_days": round(avg_loser_days, 1),
            "avg_winner_pnl_pct": avg_winner_pnl,
            "avg_loser_pnl_pct": avg_loser_pnl,
            "max_trades_per_day": max_trades_per_day,
            "avg_trades_per_day": avg_trades_per_day,
        },
        "behavioral_diagnostics": {
            "disposition_effect": {
                "ratio": disp_ratio,
                "detected": disp_ratio > 1.5,
                "detail": f"赢家持{avg_winner_days:.1f}天 vs 输家持{avg_loser_days:.1f}天",
            },
            "bearish_market_entries": {
                "count": bearish_entries,
                "total_loss": round(bearish_loss, 2),
                "detected": bearish_entries >= 3,
            },
            "strategy_deviation": {
                "rate": deviation_rate,
                "instances": deviations,
                "detected": deviation_rate > 0.2,
            },
            "overtrading": {
                "max_trades_per_day": max_trades_per_day,
                "avg_trades_per_day": avg_trades_per_day,
                "detected": max_trades_per_day > 5,
            },
        },
        "strategy_pnl_breakdown": strategy_breakdown,
        "pnl_by_weekday": {k: round(v, 2) for k, v in sorted(weekday_pnl.items())},
        "recurring_patterns": recurring,
    }


def _detect_patterns(paired, disp_ratio, bearish_entries, bearish_loss,
                     deviations, max_trades_per_day, avg_loser_pnl,
                     winner_pnl_pcts, loser_pnl_pcts, strategy_breakdown) -> list[dict]:
    """Detect recurring losing patterns from metrics."""
    patterns = []

    if disp_ratio > 1.5:
        patterns.append({
            "pattern": "处置效应-亏了不肯卖",
            "severity": "high" if disp_ratio > 2.0 else "medium",
            "evidence": f"输家持有时长是赢家的{disp_ratio:.1f}倍",
            "suggested_fix": "在stop_loss规则中增加最大持有天数限制，亏损超3天自动减仓50%",
        })

    if bearish_entries >= 3:
        patterns.append({
            "pattern": "弱势市场逆势开仓",
            "severity": "high" if bearish_entries >= 5 else "medium",
            "evidence": f"bearish日开仓{bearish_entries}次，累计亏损{bearish_loss:,.0f}元",
            "suggested_fix": "bearish时position_cap强制≤20%，禁止新增B/C类候选",
        })

    if len(paired) > 0 and deviations > len(paired) * 0.2:
        patterns.append({
            "pattern": "策略执行偏离",
            "severity": "medium",
            "evidence": f"{deviations}次交易因恐慌/止损/紧急退出，占{deviations/len(paired)*100:.0f}%",
            "suggested_fix": "强化入场前确认流程：买入前必须回测该策略历史胜率",
        })

    if max_trades_per_day > 5:
        patterns.append({
            "pattern": "过度交易",
            "severity": "medium",
            "evidence": f"单日最多{max_trades_per_day}笔交易",
            "suggested_fix": "单日开仓上限设为3笔，超过则需人工确认",
        })

    # Risk/reward: avg loser bigger than avg winner
    if loser_pnl_pcts and winner_pnl_pcts:
        avg_win = sum(winner_pnl_pcts) / len(winner_pnl_pcts)
        avg_loss = abs(sum(loser_pnl_pcts) / len(loser_pnl_pcts))
        if avg_loss > avg_win:
            patterns.append({
                "pattern": "盈亏比倒挂",
                "severity": "high",
                "evidence": f"平均盈利{avg_win:.1f}% < 平均亏损{avg_loss:.1f}%",
                "suggested_fix": "收紧止盈目标至平均盈利水平，严格执行止损不扩大",
            })

    # Check for consistently losing strategies
    for sname, sstats in strategy_breakdown.items():
        if sstats["trades"] >= 5 and sstats["win_rate"] < 35:
            patterns.append({
                "pattern": f"策略失效-{sname}",
                "severity": "high",
                "evidence": f"{sname}策略{sstats['trades']}次交易，胜率仅{sstats['win_rate']:.0f}%，累计盈亏{sstats['total_pnl']:,.0f}",
                "suggested_fix": f"暂停{sname}策略信号，回测验证后重新启用",
            })

    return patterns


def format_for_prompt(diagnostics: dict) -> str:
    """Format diagnostics as compact Chinese text for Sub-Agent C prompt injection.
    Target ~800 chars, focused on actionable findings."""
    if not diagnostics.get("paired_trades_count"):
        return ""

    s = diagnostics["summary"]
    patterns = diagnostics.get("recurring_patterns", [])

    lines = [
        f"共{diagnostics['paired_trades_count']}笔已完成交易，胜率{s['win_rate']}%，"
        f"累计盈亏{s['total_pnl']:,.0f}元。",
        f"赢家均持{s['avg_winner_hold_days']}天/均利{s['avg_winner_pnl_pct']}%，"
        f"输家均持{s['avg_loser_hold_days']}天/均亏{abs(s['avg_loser_pnl_pct'])}%。",
    ]

    if patterns:
        lines.append(f"\n检测到{len(patterns)}个重复错误模式:")
        for i, p in enumerate(patterns, 1):
            lines.append(f"  {i}. [{p['pattern']}] {p['evidence']}")
            if p.get("suggested_fix"):
                lines.append(f"     建议: {p['suggested_fix']}")

    # Strategy P&L ranking (top 3 and bottom 3)
    strat_items = sorted(diagnostics.get("strategy_pnl_breakdown", {}).items(),
                         key=lambda x: x[1]["total_pnl"])
    if len(strat_items) >= 2:
        lines.append("\n策略盈亏排名:")
        for name, st in strat_items:
            lines.append(f"  {name}: {st['trades']}笔 胜率{st['win_rate']}% 盈亏{st['total_pnl']:,.0f}")

    return "\n".join(lines)


def save_diagnostics(run_id: str, diagnostics: dict, sub_c_output: str = "") -> str:
    """Save shadow diagnostics to output directory. Returns file path."""
    out_dir = os.path.join(PROJECT_DIR, "data", "output", run_id, "shadow_account")
    os.makedirs(out_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")

    diag = dict(diagnostics)
    diag["run_id"] = run_id
    diag["data_date"] = date_str
    diag["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    fpath = os.path.join(out_dir, f"shadow_{date_str}.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(diag, f, ensure_ascii=False, indent=2, default=str)

    if sub_c_output:
        diag_path = os.path.join(out_dir, f"diagnosis_{date_str}.md")
        with open(diag_path, "w", encoding="utf-8") as f:
            f.write(f"# Shadow Diagnosis {date_str}\n\n{sub_c_output}\n")

    # Update patterns.json
    merge_patterns(run_id, diagnostics.get("recurring_patterns", []), sub_c_output)

    return fpath


def load_accumulated_patterns(run_id: str) -> list[dict]:
    """Read accumulated patterns.json for a run."""
    path = os.path.join(PROJECT_DIR, "data", "output", run_id,
                        "shadow_account", "patterns.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("patterns", [])
    except (json.JSONDecodeError, OSError):
        return []


def merge_patterns(run_id: str, new_patterns: list[dict],
                   sub_c_output: str = "") -> str:
    """Merge new patterns into accumulated patterns.json. Returns path."""
    if not new_patterns:
        return ""

    out_dir = os.path.join(PROJECT_DIR, "data", "output", run_id, "shadow_account")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "patterns.json")

    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = {p["name"]: p for p in json.load(f).get("patterns", [])}
        except (json.JSONDecodeError, OSError):
            pass

    today = datetime.now().strftime("%Y-%m-%d")
    for p in new_patterns:
        name = p.get("pattern", "")
        if name in existing:
            ex = existing[name]
            ex["last_seen"] = today
            ex["occurrence_count"] = ex.get("occurrence_count", 0) + 1
            if p.get("suggested_fix"):
                ex["suggested_fix"] = p["suggested_fix"]
            ex["status"] = "active"
        else:
            existing[name] = {
                "name": name,
                "first_seen": today,
                "last_seen": today,
                "occurrence_count": 1,
                "severity": p.get("severity", "medium"),
                "evidence": p.get("evidence", ""),
                "suggested_fix": p.get("suggested_fix", ""),
                "status": "active",
            }

    merged = {
        "run_id": run_id,
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "patterns": sorted(existing.values(), key=lambda x: x.get("severity", ""),
                          reverse=True),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, default=str)
    return path


# ═══════════════════════════════════════════════════════════════
# Phase B: deferred reflection loop (TradingAgents-inspired)
# Phase A (above) computes diagnostics. Phase B resolves past
# decisions against market outcomes and generates LLM reflections
# that get injected into the next pipeline run.
# ═══════════════════════════════════════════════════════════════


def build_reflection_prompt(diagnostics: dict) -> str:
    """Build a prompt for the LLM to reflect on past trading patterns.

    Returns a short prompt that asks the quick-thinking model to generate
    2-4 sentence reflections on what went wrong and how to improve.
    """
    if not diagnostics.get("paired_trades_count"):
        return ""

    s = diagnostics["summary"]
    patterns = diagnostics.get("recurring_patterns", [])

    prompt = (
        f"你是交易复盘分析师。以下是上一轮模拟交易的结果摘要：\n\n"
        f"已完成{s['win_rate']}%胜率共{diagnostics['paired_trades_count']}笔交易，"
        f"累计盈亏{s['total_pnl']:,.0f}元。"
        f"赢家均持{s['avg_winner_hold_days']}天/均利{s['avg_winner_pnl_pct']}%，"
        f"输家均持{s['avg_loser_hold_days']}天/均亏{abs(s['avg_loser_pnl_pct'])}%。\n"
    )

    if patterns:
        prompt += "\n检测到以下重复错误模式：\n"
        for p in patterns:
            prompt += f"- {p['pattern']}: {p.get('evidence', '')}\n"
            if p.get("suggested_fix"):
                prompt += f"  建议修复: {p['suggested_fix']}\n"

    prompt += (
        "\n请生成2-4句话的复盘反思，重点回答："
        "1) 本轮最致命的错误是什么？"
        "2) 下次应该如何避免？"
        "输出纯文本反思，不超过150字。"
    )
    return prompt


def resolve_with_llm(diagnostics: dict) -> str:
    """Generate LLM reflection on past trading patterns using quick model.

    Returns reflection text or empty string on failure.
    """
    prompt = build_reflection_prompt(diagnostics)
    if not prompt:
        return ""

    try:
        from alphaclaude.tools.llm_client import call_text
        from config import QUICK_THINK_MODEL
        reflection = call_text(prompt, model=QUICK_THINK_MODEL, max_tokens=512)
        return reflection.strip()
    except Exception:
        return ""


def format_reflections_for_prompt(reflection_text: str) -> str:
    """Format Phase B reflections for injection into Sub-Agent C prompt.

    Returns empty string if no reflection available.
    """
    if not reflection_text or not reflection_text.strip():
        return ""
    return (
        "【上轮交易复盘反思】\n"
        f"{reflection_text.strip()}\n"
        "请在本轮决策中考虑以上反思，避免重复犯同样错误。\n"
    )


def load_latest_diagnostics(run_id: str) -> dict | None:
    """Load the most recent shadow diagnostics JSON for a run."""
    out_dir = os.path.join(PROJECT_DIR, "data", "output", run_id, "shadow_account")
    if not os.path.isdir(out_dir):
        return None
    files = sorted(
        [f for f in os.listdir(out_dir) if f.startswith("shadow_") and f.endswith(".json")],
        reverse=True,
    )
    if not files:
        return None
    path = os.path.join(out_dir, files[0])
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def run_phase_b(run_id: str) -> str:
    """Phase B: load latest diagnostics, generate LLM reflection, return prompt text.

    Called by OvernightPipeline before building Sub-Agent C prompt.
    Returns reflection text ready for prompt injection, or '' if insufficient data.
    """
    diag = load_latest_diagnostics(run_id)
    if not diag or diag.get("paired_trades_count", 0) < 4:
        return ""

    reflection = resolve_with_llm(diag)
    if not reflection:
        return ""

    # Save reflection alongside diagnostics
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(PROJECT_DIR, "data", "output", run_id, "shadow_account")
    os.makedirs(out_dir, exist_ok=True)
    ref_path = os.path.join(out_dir, f"reflection_{date_str}.md")
    try:
        with open(ref_path, "w", encoding="utf-8") as f:
            f.write(f"# Phase B Reflection {date_str}\n\n{reflection}\n")
    except OSError:
        pass

    return format_reflections_for_prompt(reflection)


def compare_runs(run_a: str, run_b: str) -> dict:
    """Compare patterns.json between two runs. Returns diff report."""
    patterns_a = {p["name"]: p for p in load_accumulated_patterns(run_a)}
    patterns_b = {p["name"]: p for p in load_accumulated_patterns(run_b)}

    resolved = []
    new_patterns = []
    persistent = []

    for name, pa in patterns_a.items():
        if name in patterns_b:
            persistent.append({"name": name, "before": pa, "after": patterns_b[name]})
        else:
            resolved.append({"name": name, "detail": pa})

    for name, pb in patterns_b.items():
        if name not in patterns_a:
            new_patterns.append({"name": name, "detail": pb})

    return {
        "run_a": run_a, "run_b": run_b,
        "resolved": resolved,
        "persistent": persistent,
        "new": new_patterns,
        "summary": f"{len(resolved)} resolved, {len(persistent)} persistent, {len(new_patterns)} new",
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Shadow Account — behavioral trade diagnostics")
    parser.add_argument("run_id", help="Run ID (directory name under data/output/)")
    parser.add_argument("--output", "-o", choices=["text", "json", "both"],
                        default="text", help="Output format (default: text)")
    parser.add_argument("--save", "-s", action="store_true",
                        help="Save diagnostics to shadow_account/ directory")
    parser.add_argument("--patterns", "-p", action="store_true",
                        help="Show accumulated patterns only")
    parser.add_argument("--compare", "-c", default="",
                        help="Compare patterns with another run_id")
    parser.add_argument("--resolve", "-r", action="store_true",
                        help="Phase B: generate LLM reflection from latest diagnostics")
    args = parser.parse_args()

    if args.resolve:
        reflection = run_phase_b(args.run_id)
        if reflection:
            print(reflection)
        else:
            print("(insufficient data for Phase B reflection — need >= 4 paired trades)")
        return

    if args.compare:
        result = compare_runs(args.run_id, args.compare)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    entries = load_ledger(args.run_id)
    if not entries:
        print(json.dumps({"error": f"No ledger found for {args.run_id}"},
                        ensure_ascii=False))
        sys.exit(1)

    paired, open_pos = pair_trades(entries)
    diagnostics = compute_diagnostics(paired, open_pos, entries)

    if args.patterns:
        patterns = load_accumulated_patterns(args.run_id)
        print(json.dumps(patterns, ensure_ascii=False, indent=2, default=str))
        return

    if args.save:
        fpath = save_diagnostics(args.run_id, diagnostics)
        print(f"Saved: {fpath}")

    if args.output in ("text", "both"):
        print(format_for_prompt(diagnostics))

    if args.output in ("json", "both"):
        print(json.dumps(diagnostics, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
