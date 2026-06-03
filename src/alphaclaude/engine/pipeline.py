"""Pre-market plan generation and emergency Claude pipeline orchestration."""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from alphaclaude.paths import PROJECT_ROOT, add_legacy_paths
from alphaclaude.engine.clock import TradingClock
from alphaclaude.engine.data_feed import BacktestDataFeed
from alphaclaude.engine.execution import ExecutionEngine
from alphaclaude.engine.ledger import Ledger
from alphaclaude.engine.plan import PlanManager
from alphaclaude.engine.state import EngineState
from alphaclaude.engine.workflow_events import WorkflowEventStore

add_legacy_paths()

try:
    from alphaclaude.tools.notifier import notify_overnight_timeout, notify_sub_agent_summaries
    _notify = True
except Exception:
    _notify = False

class OvernightPipeline:
    """v3 after-hours pipeline: sub-agent research → merged Stage → Python risk validation.

    Phase 0: 3 parallel claude -p sub-agents (policy, sector, review) → ~500 char summaries
    Phase 1: Single merged Claude Code call → direction + candidates + adjustments
    Phase 2: risk.py + signal.py hard validation → final plan.json
    Emergency: Market/stock anomaly triggers Claude Code during trading hours.
    """

    def __init__(self, state: EngineState, plan: PlanManager,
                 ledger: Ledger, clock: TradingClock, output_dir: str,
                 mode: str = "paper", execution: "ExecutionEngine" = None,
                 data_feed: "BacktestDataFeed" = None):
        self.state = state
        self.plan = plan
        self.ledger = ledger
        self.clock = clock
        self.output_dir = output_dir
        self.mode = mode
        self.execution = execution
        self.data_feed = data_feed
        self.run_id = os.path.basename(output_dir)
        self.workflow = WorkflowEventStore(output_dir, run_id=self.run_id)
        self._last_shadow_diagnostics = ""
        try:
            from alphaclaude.tools.strategy_variants import get_active_variant
            self.variant = get_active_variant()
        except Exception:
            self.variant = {
                "name": "默认", "position_cap_by_bias": {"bullish": 80, "neutral": 50, "bearish": 20},
                "source_b_max_pct": 20.0, "source_b_stop_pct": -8,
                "source_c_max_pct": 7.5, "source_c_stop_pct": -5,
                "max_single_position_pct": 25.0, "signal_min_confidence": 65,
                "signal_position_pct": 0.075, "max_total_position_pct": 80.0,
            }

    def _bc_rules_text(self) -> str:
        """B/C source rules text from active variant for LLM prompts."""
        v = self.variant
        return (
            f"B类上限{v.get('source_b_max_pct',20):.0f}%"
            f"止损{v.get('source_b_stop_pct',-8)}%, "
            f"C类上限{v.get('source_c_max_pct',7.5):.0f}%"
            f"止损{v.get('source_c_stop_pct',-5)}%。"
            "止损止盈必须用百分比(stop_loss_pct/take_profit_pct)，禁止填绝对价。"
        )

    # ── Skill injection cache ────────────────────────────────────

    _skill_injections: dict[str, str] | None = None

    @classmethod
    def _get_skill_injections(cls) -> dict[str, str]:
        """Load and cache skill reference content for prompt injection.

        Returns dict with keys: 'entry_signals', 'screening', 't0_params', 'risk_checklist'.
        Content is extracted from skills/ directory once and cached at class level.
        """
        if cls._skill_injections is not None:
            return cls._skill_injections
        import os as _os
        skills_dir = str(PROJECT_ROOT / "skills")
        inj = {}
        try:
            # Entry signals scoring table
            entry_path = _os.path.join(skills_dir, "stock-analyzer", "references", "entry-signals.md")
            if _os.path.exists(entry_path):
                with open(entry_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # Extract just the signal summary table (lines starting with | # |)
                sig_lines = [line for line in content.split("\n") if line.startswith("| ") and not line.startswith("|---")]
                if sig_lines:
                    inj["entry_signals"] = (
                        "## 入场信号评分 (skills/stock-analyzer/entry-signals)\n"
                        + "\n".join(sig_lines[:5])
                    )
            # T+0 intraday params
            t0_path = _os.path.join(skills_dir, "t+0-intraday", "SKILL.md")
            if _os.path.exists(t0_path):
                with open(t0_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # Extract the volatility bucket table
                in_table = False
                t0_lines = []
                for line in content.split("\n"):
                    if "ATR波动率" in line and "做T目标" in line:
                        in_table = True
                        t0_lines.append(line)
                        continue
                    if in_table:
                        if line.startswith("|") and not line.startswith("|---"):
                            t0_lines.append(line)
                        elif not line.startswith("|"):
                            in_table = False
                if t0_lines:
                    inj["t0_params"] = (
                        "## T+0波动率分档参数 (skills/t+0-intraday)\n" + "\n".join(t0_lines)
                    )
            # Risk checklist
            risk_path = _os.path.join(skills_dir, "stock-analyzer", "references", "risk-checklist.md")
            if _os.path.exists(risk_path):
                with open(risk_path, "r", encoding="utf-8") as f:
                    content = f.read()
                risk_items = [line.strip() for line in content.split("\n")
                              if line.strip().startswith("- ") and len(line) > 10][:8]
                if risk_items:
                    inj["risk_checklist"] = (
                        "## 风险排查清单 (skills/stock-analyzer/risk-checklist)\n"
                        + "\n".join(risk_items)
                    )
        except Exception:
            pass
        cls._skill_injections = inj
        return inj

    # ── Phase 0: Sub-Agent Research ───────────────────────────────

    # ── Shared data fetchers (used by sub-agents + merged stage) ──

    def _fetch_market_snapshot(self) -> str:
        """Fetch market index + north-bound flow data for prompt injection.

        In backtest mode, uses the data_feed's historical index cache for the
        simulated date. In paper/live mode, fetches live market data.
        """
        lines = []

        is_bt = self.mode == "backtest" and self.data_feed is not None
        if is_bt:
            # ── Backtest: use historical index data (lazy-loaded) ──
            sim_date = self.clock.now()
            sim_ts = pd.Timestamp(sim_date.strftime("%Y-%m-%d"))
            idx_q = self.data_feed.get_index_quote(sim_ts)
            if idx_q:
                lines.append(f"  上证指数: {idx_q['price']:.2f} {idx_q['change_pct']:+.2f}% "
                             f"(开{idx_q['open']:.2f} 高{idx_q['high']:.2f} 低{idx_q['low']:.2f})")
            else:
                lines.append("  (当日无指数数据)")
            lines.append("  北向资金: 回测模式不可用")
        else:
            # ── Live: fetch from APIs ──
            from alphaclaude.tools._compression import compress_output
            try:
                from alphaclaude.tools.quote import get_market_overview
                overview = get_market_overview()
                if overview and not overview.get("error"):
                    lines.append(compress_output(overview, "market_overview"))
            except Exception:
                lines.append("  (行情数据暂不可用)")
            lines.append("")
            try:
                from alphaclaude.tools.flow import get_north_flow
                nf = get_north_flow()
                if nf and not nf.get("error"):
                    lines.append(compress_output(nf, "north_flow"))
            except Exception:
                pass

        return "\n".join(lines)

    # ── Sub-agent prompt builders ─────────────────────────────────

    def _build_sub_agent_a_prompt(self) -> str:
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        market = self._fetch_market_snapshot()
        return (
            f"{sim_date} A股宏观政策分析。≤500字摘要。\n"
            f"大盘:\n{market}\n"
            f"要求: 1.政策方向 2.风险偏好(risk-on/off) 3.1个关键事件\n"
            f"数据不足时基于最近市场趋势和常识合理推断，标注[推断]。"
        )

    def _build_sub_agent_b_prompt(self) -> str:
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        market = self._fetch_market_snapshot()
        return (
            f"{sim_date} A股板块轮动。≤500字摘要。\n"
            f"大盘:\n{market}\n"
            f"要求: 1.强势板块3个+弱势2个 2.风格切换(大/小盘,成长/价值) 3.次日关注板块3个+理由\n"
            f"数据不足时基于最近市场趋势和常识合理推断，标注[推断]。"
        )

    def _build_sub_agent_c_prompt(self) -> str:
        s = self.state.load()
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        recent = self.ledger.read_recent(5)
        lines = [
            f"{sim_date} 交易复盘。≤500字摘要。",
            f"总资产:{self.state.total_value:,.0f} 现金:{s['cash']:,.0f}",
            "数据不足时基于常识合理推断，标注[推断]。",
        ]

        # Try Shadow Account diagnostics (Phase A) + Phase B reflection
        shadow_text = self._try_shadow_diagnostics()
        reflection_text = self._try_phase_b_reflection()

        if shadow_text:
            lines.append(f"\n[影子账户行为诊断]\n{shadow_text}")
            if reflection_text:
                lines.append(f"\n{reflection_text}")
            lines.append("要求: 1.验证诊断是否准确 2.确认/否定每个模式 3.提出1条可操作的prompt改进建议")
            self._last_shadow_diagnostics = shadow_text
        else:
            self._last_shadow_diagnostics = ""
            if s["holdings"]:
                lines.append("持仓:")
                for code, h in s["holdings"].items():
                    pnl = (h['current_price'] - h['avg_cost']) / h['avg_cost'] * 100 if h['avg_cost'] > 0 else 0
                    lines.append(f"  {code}: {h['shares']}股 成本{h['avg_cost']:.2f} 现价{h['current_price']:.2f} {pnl:+.1f}%")
            else:
                lines.append("空仓")
            if recent:
                lines.append("近5决策:")
                for e in recent[-5:]:
                    lines.append(f"  [{e['seq']}] {e.get('decision','')} {e.get('reasoning','')[:60]}")
            lines.append("要求: 1.决策回顾 2.持仓评估 3.经验教训1条")
        return "\n".join(lines)

    def _try_shadow_diagnostics(self) -> str:
        """Compute shadow diagnostics from ledger. Returns '' if insufficient data."""
        try:
            all_entries = self.ledger.read_all()
            trade_entries = [e for e in all_entries
                           if e.get("decision") in ("open_position", "close_position")]
            if len(trade_entries) < 8:
                return ""
            from alphaclaude.tools.shadow_account import pair_trades, compute_diagnostics, format_for_prompt
            paired, open_pos = pair_trades(all_entries)
            diagnostics = compute_diagnostics(paired, open_pos, all_entries)
            return format_for_prompt(diagnostics)
        except Exception:
            return ""

    def _try_phase_b_reflection(self) -> str:
        """Phase B: generate LLM reflection from previous shadow diagnostics.

        Loads the most recent shadow diagnostics, asks quick-thinking LLM to
        generate 2-4 sentence trading lessons, and returns formatted text for
        injection into Sub-Agent C prompt. Returns '' if no prior data.
        """
        try:
            all_entries = self.ledger.read_all()
            trade_entries = [e for e in all_entries
                           if e.get("decision") in ("open_position", "close_position")]
            if len(trade_entries) < 8:
                return ""
            from alphaclaude.tools.shadow_account import run_phase_b
            return run_phase_b(self.run_id)
        except Exception:
            return ""

    def _run_sub_agents(self) -> dict[str, str]:
        """Run 3 sub-agents in parallel via SDK direct call (QUICK_THINK_MODEL).

        Returns {'A': summary, 'B': summary, 'C': summary}.
        """
        from alphaclaude.tools.llm_client import call_text
        from alphaclaude.config import QUICK_THINK_MODEL

        prompts = {
            "A": self._build_sub_agent_a_prompt(),
            "B": self._build_sub_agent_b_prompt(),
            "C": self._build_sub_agent_c_prompt(),
        }
        results = {}

        def _run_one(label, prompt):
            for attempt in range(2):
                try:
                    text = (call_text(prompt, max_tokens=800, model=QUICK_THINK_MODEL) or "").strip()
                    if text and "超时" not in text and "出错" not in text:
                        return label, text[:500]
                    if attempt == 0:
                        print(f"  Sub-agent {label} attempt {attempt+1}: timeout/error, retrying...")
                        time.sleep(5)
                    else:
                        return label, text[:500] if text else f"(失败: {text[:80]})"
                except Exception as exc:
                    if attempt == 0:
                        print(f"  Sub-agent {label} attempt {attempt+1}: {exc}, retrying...")
                        time.sleep(5)
                    else:
                        print(f"[SubAgent {label}] failed: {exc}")
                        return label, f"(数据不可用: {exc})"
            return label, "(超时)"

        print("[OvernightPipeline] Phase 0: Running 3 sub-agents in parallel...")
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(_run_one, label, p): label for label, p in prompts.items()}
            from concurrent.futures import wait as fut_wait
            done, not_done = fut_wait(futures, timeout=360)
            for f in done:
                label, summary = f.result()
                results[label] = summary
                print(f"  Sub-agent {label}: {len(summary)} chars")
            for f in not_done:
                label = futures[f]
                results[label] = "(超时)"
                f.cancel()
                print(f"  Sub-agent {label}: timed out")

        dbg_path = os.path.join(self.output_dir, "_sub_agent_summaries.txt")
        with open(dbg_path, "w", encoding="utf-8") as f:
            for label, summary in results.items():
                f.write(f"=== Sub-agent {label} ===\n{summary}\n\n")
        if _notify:
            try:
                notify_sub_agent_summaries(self.run_id, results)
            except Exception:
                pass
        return results

    # ── Phase 1: Merged Decision Stage ────────────────────────────

    def _fetch_candidates_screen(self) -> str:
        """Fetch screen results for merged prompt injection.

        In backtest mode, compiles top-volume/change stocks from the data_feed's
        historical data instead of calling live screen APIs.
        """
        lines = []
        is_bt = self.mode == "backtest" and self.data_feed is not None

        if is_bt:
            # ── Backtest: strategy-based screening (matching skills/stock-screener/breakout) ──
            sim_date = self.clock.now()
            sim_ts = pd.Timestamp(sim_date.strftime("%Y-%m-%d"))
            PRICE_MIN, PRICE_MAX = 3, 300
            CHG_MIN, CHG_MAX = 0.5, 9.5
            VOL_RATIO_MIN = 1.1
            AMOUNT_MIN = 5e7  # 5000万

            scored = []
            for code in self.data_feed.universe:
                self.data_feed._ensure_loaded(code)
                df = self.data_feed._daily_cache.get(code)
                if df is None or df.empty:
                    continue
                hist = df[df["date"] <= sim_ts]
                if len(hist) < 25:
                    continue
                r = hist.iloc[-1]
                price = float(r["close"])
                high = float(r.get("high", price))
                low = float(r.get("low", price))
                volume = float(r.get("volume", 0))
                if price <= 0 or volume <= 0:
                    continue
                # Price filter
                if price < PRICE_MIN or price > PRICE_MAX:
                    continue
                # Daily change filter
                prev_close = float(hist.iloc[-2]["close"])
                chg_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
                if chg_pct < CHG_MIN or chg_pct > CHG_MAX:
                    continue
                # Amount filter (volume * price ≈ trading amount)
                if volume * price < AMOUNT_MIN:
                    continue
                # Volume ratio filter
                vol_5d = [float(x) for x in hist["volume"].iloc[-6:-1].tolist()]
                avg_vol_5 = sum(vol_5d) / 5 if vol_5d else 0
                vol_ratio = volume / avg_vol_5 if avg_vol_5 > 0 else 0
                if vol_ratio < VOL_RATIO_MIN:
                    continue
                # MA computation
                closes_arr = [float(x) for x in hist["close"].tolist()]
                ma5 = sum(closes_arr[-5:]) / 5
                ma10 = sum(closes_arr[-10:]) / 10
                ma20 = sum(closes_arr[-20:]) / 20
                # MA alignment filter: at least MA5 > MA10
                ma_ok = ma5 > ma10
                # Signal scoring (matching skills/stock-analyzer/entry-signals)
                signal_score = 0
                signals = []
                # Golden cross: MA5 crossed above MA10 in last 3 days
                ma5_1d = sum(closes_arr[-6:-1]) / 5
                ma10_1d = sum(closes_arr[-11:-1]) / 10
                if ma5 > ma10 and ma5_1d <= ma10_1d:
                    signal_score += 10
                    signals.append("金叉")
                # Volume breakout: vol_ratio >= 2.0, close in upper 30% of range
                range_pos = (price - low) / (high - low) * 100 if high > low else 50
                if vol_ratio >= 2.0 and range_pos >= 70:
                    signal_score += 12
                    signals.append("放量突破")
                # Shrink pullback: price near MA5, vol_ratio < 0.8
                if ma5 > 0 and abs(price - ma5) / ma5 * 100 < 1.5 and vol_ratio < 0.8:
                    signal_score += 10
                    signals.append("缩量回踩")
                # Composite score
                base = (vol_ratio * 10) + abs(chg_pct) + (10 if ma_ok else -5)
                final_score = base + signal_score
                ma_status = "多头" if ma5 > ma10 > ma20 else ("MA5>MA10" if ma_ok else "震荡")
                scored.append({
                    "code": code, "price": price, "chg_pct": chg_pct,
                    "vol_ratio": vol_ratio, "ma": ma_status,
                    "signals": signals, "score": final_score,
                })
            scored.sort(key=lambda x: x["score"], reverse=True)
            if scored:
                lines.append(
                    f"## 策略筛选候选 (breakout, sim_date={sim_date.strftime('%Y-%m-%d')})")
                lines.append(
                    f"  条件: 涨幅{CHG_MIN}-{CHG_MAX}% 量比>{VOL_RATIO_MIN} "
                    f"成交额>{AMOUNT_MIN/1e8:.1f}亿 价格{PRICE_MIN}-{PRICE_MAX}")
                lines.append(f"  共{len(scored)}只通过初筛, 展示前20 (信号评分基于entry-signals):")
                for c in scored[:20]:
                    sig_str = ",".join(c["signals"]) if c["signals"] else "—"
                    lines.append(
                        f"  {c['code']}: {c['price']:.2f} {c['chg_pct']:+.2f}% "
                        f"量比{c['vol_ratio']:.1f} MA:{c['ma']} "
                        f"信号:[{sig_str}] 评分:{c['score']:.0f}")
            if len(scored) < 8:
                # Fallback: broader volume-based ranking for reference
                fallback = []
                for code in self.data_feed.universe:
                    self.data_feed._ensure_loaded(code)
                    df = self.data_feed._daily_cache.get(code)
                    if df is None or df.empty:
                        continue
                    hist = df[df["date"] <= sim_ts]
                    if len(hist) < 5:
                        continue
                    r = hist.iloc[-1]
                    p = float(r["close"])
                    v = float(r.get("volume", 0))
                    if p <= 0 or v <= 0 or p < PRICE_MIN or p > PRICE_MAX:
                        continue
                    prev_c = float(hist.iloc[-2]["close"])
                    chg = (p - prev_c) / prev_c * 100 if prev_c else 0
                    if v * p < AMOUNT_MIN:
                        continue
                    if code not in {s["code"] for s in scored}:
                        fallback.append({"code": code, "price": p, "chg_pct": chg, "vol": v})
                fallback.sort(key=lambda x: x["vol"], reverse=True)
                if fallback:
                    if not scored:
                        lines.append("## 策略筛选候选 (无标的通过策略过滤, 展示量能排名参考)")
                    else:
                        lines.append(f"## 补充候选 (策略筛选仅{len(scored)}只, 量能排名补充)")
                    for f in fallback[:max(10, 15-len(scored))]:
                        lines.append(f"  {f['code']}: {f['price']:.2f} {f['chg_pct']:+.2f}% 量{f['vol']/1e6:.1f}M")
            if not scored and not fallback:
                lines.append("## 策略筛选候选 (当日无数据)")
        else:
            # ── Live: run screen.py ──
            from alphaclaude.tools._compression import compress_output
            try:
                from alphaclaude.tools.screen import run_screen
                result = run_screen("default")
                if isinstance(result, dict) and result.get("results"):
                    lines.append(compress_output(result, "screen"))
                elif not isinstance(result, dict) or not result.get("results"):
                    lines.append("## 技术筛选候选 (无结果)")
            except Exception:
                lines.append("## 技术筛选候选 (不可用)")

        return "\n".join(lines)

    def _build_shadow_feedback(self) -> str:
        """Read accumulated shadow patterns, return anti-pattern rules for prompt injection."""
        import json as _json
        patterns_path = os.path.join(
            self.output_dir, "shadow_account", "patterns.json")
        if not os.path.exists(patterns_path):
            return ""
        try:
            with open(patterns_path, "r", encoding="utf-8") as _f:
                data = _json.load(_f)
        except (OSError, ValueError):
            return ""

        active = [p for p in data.get("patterns", []) if p.get("status") == "active"]
        if not active:
            return ""

        lines = ["## [影子账户] 历史重复错误模式 — 务必规避"]
        for i, p in enumerate(active[:5], 1):
            name = p.get("name", "")
            count = p.get("occurrence_count", 0)
            evidence = p.get("evidence", "")
            fix = p.get("suggested_fix", "")
            lines.append(f"{i}. [{name}] 共{count}次 {evidence}")
            if fix:
                lines.append(f"   规避措施: {fix}")
        return "\n".join(lines)

    def _call_text_safe(self, prompt: str, label: str, model: str | None = None) -> str:
        """Call call_text with error handling. Returns '' on failure."""
        from alphaclaude.tools.llm_client import call_text
        try:
            result = call_text(prompt, max_tokens=2048, model=model)
            return (result or "").strip()
        except Exception as exc:
            print(f"[OvernightPipeline] {label} text call failed: {exc}")
            return ""

    def _parse_candidates(self, tool_inputs: list[dict]) -> list[dict]:
        """Parse TOOL_ADD_CANDIDATE results into candidate dicts.

        Supports both old format (absolute stop_loss/take_profit) and new
        percentage-based format (stop_loss_pct/take_profit_pct).
        """
        candidates = []
        for d in (tool_inputs or []):
            if "code" not in d:
                continue
            c = {"code": str(d["code"])}
            for k in ("source", "reasoning"):
                if k in d:
                    c[k] = str(d[k])
            for k in ("priority", "cooldown_days", "max_hold_days", "expires_after_days"):
                if k in d:
                    try:
                        c[k] = int(d[k])
                    except (ValueError, TypeError):
                        pass
            for k in ("entry_max", "entry_min", "stop_loss", "take_profit",
                      "stop_loss_pct", "take_profit_pct", "position_pct"):
                if k in d:
                    try:
                        c[k] = float(d[k])
                    except (ValueError, TypeError):
                        pass
            candidates.append(c)
        return candidates

    def _parse_jsonish_tool_text(self, text: str) -> list[dict]:
        """Parse conservative JSON/list fallback text into tool-style inputs."""
        if not text:
            return []

        candidates = [text.strip()]
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        candidates.extend(block.strip() for block in fenced if block.strip())

        array_match = re.search(r"\[[\s\S]*\]", text)
        if array_match:
            candidates.append(array_match.group(0))
        object_match = re.search(r"\{[\s\S]*\}", text)
        if object_match:
            candidates.append(object_match.group(0))

        for raw in candidates:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            if isinstance(data, dict):
                for key in ("tool_inputs", "candidates", "adjustments", "actions", "data"):
                    value = data.get(key)
                    if isinstance(value, list):
                        return [item for item in value if isinstance(item, dict)]
                return [data]
        return []

    def _parse_direction_fallback(self, text: str) -> list[dict]:
        """Fallback direction parser. Never invents aggressive exposure."""
        parsed = self._parse_jsonish_tool_text(text)
        if parsed:
            return parsed[:1]

        lower = text.lower()
        if any(k in lower for k in ("bearish", "看空", "偏空", "防守", "风险偏高")):
            bias = "bearish"
            cap = 20
        elif any(k in lower for k in ("bullish", "看多", "偏多", "进攻", "风险偏好")):
            bias = "bullish"
            cap = 50
        else:
            bias = "neutral"
            cap = 30

        pct_match = re.search(r"(\d{1,3})\s*[%％]", text)
        if pct_match:
            cap = max(0, min(100, int(pct_match.group(1))))

        return [{
            "bias": bias,
            "confidence": 50,
            "bias_reasoning": (text or "fallback neutral direction")[:300],
            "position_cap": cap,
            "prefer_sectors": [],
            "avoid_sectors": [],
        }]

    def _parse_candidates_fallback(self, text: str) -> list[dict]:
        return self._parse_candidates(self._parse_jsonish_tool_text(text))

    def _parse_adjustments_fallback(self, text: str) -> list[dict]:
        parsed = self._parse_jsonish_tool_text(text)
        allowed = {"raise_stop", "close", "hold"}
        adjustments = []
        for item in parsed:
            code = item.get("code")
            if not code:
                continue
            action = str(item.get("action", "hold"))
            if action not in allowed:
                action = "hold"
            adj = {
                "code": str(code),
                "action": action,
                "reasoning": str(item.get("reasoning") or "fallback parsed adjustment"),
            }
            if "new_stop_loss" in item:
                try:
                    adj["new_stop_loss"] = float(item["new_stop_loss"])
                except (TypeError, ValueError):
                    pass
            adjustments.append(adj)
        return adjustments

    def _parse_emergency_fallback(self, text: str) -> list[dict]:
        parsed = self._parse_jsonish_tool_text(text)
        if parsed:
            item = dict(parsed[0])
            if item.get("action") not in {"hold", "reduce", "close", "close_all"}:
                item["action"] = "hold"
            item.setdefault("reasoning", "fallback parsed emergency decision")
            return [item]
        return [{"action": "hold", "reasoning": (text or "emergency fallback hold")[:300]}]

    @staticmethod
    def _bounded_int(value, default: int, low: int = 0, high: int = 100) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, parsed))

    def _build_bull_prompt(self, summaries: dict[str, str], direction: dict) -> str:
        """Build prompt for Bull agent: find reasons to buy each candidate."""
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        bias = direction.get("bias", "neutral")
        cap = direction.get("position_cap", 80)
        injections = self._get_skill_injections()
        parts = [
            f"{sim_date} 你是乐观的A股分析师(Bull)。为候选池中每只股票找出做多理由。",
            f"市场偏向:{bias} 仓位上限:{cap}%",
            f"板块分析: {summaries.get('B','')[:200]}",
            self._fetch_candidates_screen(),
            injections.get("entry_signals", ""),
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        parts.append(
            "要求: 1.逐只分析技术面/资金面/消息面做多理由 "
            "2.给出每只的合理买入价位和止盈目标 "
            "3.不遗漏任何候选，不筛选——你只负责找理由买入"
        )
        return "\n".join(parts)

    def _build_bear_prompt(self, summaries: dict[str, str], direction: dict,
                           bull_analysis: str) -> str:
        """Build prompt for Bear agent: find risks NOT to buy each candidate."""
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        bias = direction.get("bias", "neutral")
        cap = direction.get("position_cap", 80)
        injections = self._get_skill_injections()
        parts = [
            f"{sim_date} 你是审慎的风险分析师(Bear)。阅读Bull的做多分析后，逐只找出不应买入的理由。",
            f"市场偏向:{bias} 仓位上限:{cap}%",
            f"板块分析: {summaries.get('B','')[:200]}",
            "## Bull 做多分析",
            bull_analysis[:2000],
            "## 候选池",
            self._fetch_candidates_screen(),
            injections.get("risk_checklist", ""),
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        parts.append(
            "要求: 1.逐只找出Bull遗漏的风险点(估值/技术/资金/政策) "
            "2.对每只给出风险等级(高/中/低) "
            "3.标注哪几只应直接否决——你负责挑毛病"
        )
        return "\n".join(parts)

    def _build_risk_prompt(self, summaries: dict[str, str], direction: dict,
                           bull_analysis: str, bear_analysis: str) -> str:
        """Build prompt for Risk agent: final arbiter after debate."""
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        bias = direction.get("bias", "neutral")
        preferred = ",".join(direction.get("preferred") or []) or "无"
        cap = direction.get("position_cap", 80)
        parts = [
            f"{sim_date}次日选股。你是最终决策者(Risk)。阅读Bull和Bear的辩论后，"
            f"调用 add_candidate 工具提交最终候选。",
            f"市场偏向:{bias} 偏好板块:{preferred} 仓位上限:{cap}%",
            "## Bull 做多分析",
            bull_analysis[:2000],
            "## Bear 风险分析",
            bear_analysis[:2000],
            self._fetch_candidates_screen(),
            self._bc_rules_text(),
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        parts.append(
            "裁决原则: 1.Bear标注'直接否决'的不纳入 "
            "2.Bull理由充分+风险可控的优先, 尽量选出5-10只候选分散风险 "
            "3.每只候选调用一次add_candidate, 单只仓位5-15% (多选则仓位分摊) "
            "4.Bear仅标注'低风险'的可适当加大仓位, '中风险'的缩小仓位 "
            "5.候选不足5只时如实返回, 不强行凑数"
        )
        return "\n".join(parts)

    def _run_bull_bear_debate(self, summaries: dict[str, str],
                              direction: dict) -> tuple[list[dict], str]:
        """Run Bull/Bear/Risk three-stage debate for candidate selection.

        Bull and Bear use QUICK_THINK_MODEL (research/debate). Risk final
        decision call uses call_with_tool_safe default (ANTHROPIC_MODEL).
        Returns (candidates, debate_trace). Falls back to empty on any failure.
        """
        from alphaclaude.tools.llm_client import call_with_tool_safe, TOOL_ADD_CANDIDATE
        from alphaclaude.config import QUICK_THINK_MODEL

        bull_prompt = self._build_bull_prompt(summaries, direction)
        bull_text = self._call_text_safe(bull_prompt, "Bull", model=QUICK_THINK_MODEL)
        if not bull_text:
            return [], ""

        bear_prompt = self._build_bear_prompt(summaries, direction, bull_text)
        bear_text = self._call_text_safe(bear_prompt, "Bear", model=QUICK_THINK_MODEL)
        if not bear_text:
            return [], ""

        risk_prompt = self._build_risk_prompt(summaries, direction, bull_text, bear_text)
        tool_inputs = call_with_tool_safe(
            risk_prompt,
            [TOOL_ADD_CANDIDATE],
            fallback_parser=self._parse_candidates_fallback,
        )

        candidates = self._parse_candidates(tool_inputs or [])
        trace = f"=== BULL ===\n{bull_text}\n\n=== BEAR ===\n{bear_text}"
        return candidates, trace

    # ── 3.10 Risk Debate ──────────────────────────────────────────────

    def _build_aggressive_risk_prompt(self, candidates: list[dict],
                                       total_pct: float) -> str:
        """Build prompt for the Aggressive risk debater — champions high returns."""
        cand_summary = "\n".join(
            f"- {c['code']}: 仓位{c.get('position_pct',0)}% 入场{c.get('entry_max','?')} "
            f"止损{c.get('stop_loss','?')} 止盈{c.get('take_profit','?')} "
            f"理由:{c.get('reasoning','')[:60]}"
            for c in candidates
        )
        return (
            "你是交易风控委员会中的[激进派风控官]。\n"
            "你相信趋势的延续性和波动中的机会，倾向于在可控风险下追求更高收益。\n\n"
            f"当前待执行候选（总仓位{total_pct:.0f}%）：\n{cand_summary}\n\n"
            "请用≤300字阐述你的立场：\n"
            "1. 哪些仓位可以维持甚至放大？为什么？\n"
            "2. 当前市场环境是否支持进取策略？\n"
            "3. 给出你的综合仓位建议（维持/放大到X%）。"
        )

    def _build_conservative_risk_prompt(self, candidates: list[dict],
                                         total_pct: float,
                                         aggressive_text: str) -> str:
        """Build prompt for the Conservative risk debater — champions capital preservation."""
        cand_summary = "\n".join(
            f"- {c['code']}: 仓位{c.get('position_pct',0)}%"
            for c in candidates
        )
        return (
            "你是交易风控委员会中的[保守派风控官]。\n"
            "你把本金安全放在第一位，倾向在不确定时缩减仓位、收紧止损。\n\n"
            f"当前待执行候选（总仓位{total_pct:.0f}%）：\n{cand_summary}\n\n"
            f"激进派的观点：\n{aggressive_text[:400]}\n\n"
            "请用≤300字阐述你的立场：\n"
            "1. 激进派的哪些判断过于乐观？风险被低估在哪里？\n"
            "2. 哪些仓位应该缩减或取消？为什么？\n"
            "3. 给出你的综合仓位建议（缩减到X%或否决哪些标的）。"
        )

    def _build_neutral_risk_prompt(self, candidates: list[dict],
                                    total_pct: float,
                                    aggressive_text: str,
                                    conservative_text: str) -> str:
        """Build prompt for the Neutral risk debater — synthesizes both sides."""
        cand_summary = "\n".join(
            f"- {c['code']}: 仓位{c.get('position_pct',0)}% 入场{c.get('entry_max','?')}"
            for c in candidates
        )
        return (
            "你是交易风控委员会中的[中立裁决官]。\n"
            "你听完激进派和保守派的辩论后做出最终风险裁决。\n\n"
            f"待执行候选（总仓位{total_pct:.0f}%）：\n{cand_summary}\n\n"
            f"=== 激进派 ===\n{aggressive_text[:400]}\n\n"
            f"=== 保守派 ===\n{conservative_text[:400]}\n\n"
            "请用≤200字做出裁决：\n"
            "1. 最终仓位建议：维持 / 缩减到X% / 否决Y标的\n"
            "2. 关键风险提示（1-2句）\n"
            "3. 一句话裁决理由。"
        )

    def _run_risk_debate(self, candidates: list[dict]) -> dict | None:
        """3.10 Risk debate: Aggressive/Conservative/Neutral three-person debate
        for large-position trades. Runs only when single > 15% or total > 50%.

        Aggressive and Conservative use QUICK_THINK_MODEL (debate). Neutral
        arbiter uses ANTHROPIC_MODEL (final decision).

        Returns {'action': 'maintain'|'reduce'|'reject', 'suggested_total_pct': float,
                 'rejected_codes': [...], 'reasoning': str} or None on skip/failure.
        """
        from alphaclaude.config import QUICK_THINK_MODEL

        # Check trigger conditions
        total_pct = sum(c.get("position_pct", 0) for c in candidates)
        max_single = max((c.get("position_pct", 0) for c in candidates), default=0)
        if max_single <= 15 and total_pct <= 50:
            return None  # Below threshold — skip debate

        print(f"[RiskDebate] Triggered: max_single={max_single:.0f}% total={total_pct:.0f}%")

        aggressive_prompt = self._build_aggressive_risk_prompt(candidates, total_pct)
        aggressive_text = self._call_text_safe(aggressive_prompt, "Risk-Aggressive", model=QUICK_THINK_MODEL)
        if not aggressive_text:
            return None

        conservative_prompt = self._build_conservative_risk_prompt(
            candidates, total_pct, aggressive_text)
        conservative_text = self._call_text_safe(conservative_prompt, "Risk-Conservative", model=QUICK_THINK_MODEL)
        if not conservative_text:
            return None

        neutral_prompt = self._build_neutral_risk_prompt(
            candidates, total_pct, aggressive_text, conservative_text)
        neutral_text = self._call_text_safe(neutral_prompt, "Risk-Neutral")
        if not neutral_text:
            return None

        # Parse decision from neutral text
        reasoning = neutral_text[:200]
        action = "maintain"
        suggested_pct = total_pct
        rejected = []
        neutral_lower = neutral_text.lower()
        has_reject = any(kw in neutral_lower for kw in ["否决", "reject", "取消", "移除", "剔除"])
        has_reduce = any(kw in neutral_lower for kw in ["缩减", "reduce"])

        if has_reject or has_reduce:
            action = "reduce"
            # Parse rejected codes — split into sentences, only flag codes
            # co-occurring with rejection keywords
            import re as _re
            clauses = _re.split(r"[。；;.\n,，、]", neutral_text)
            reject_kw = ["否决", "reject", "取消", "移除", "剔除", "不建议", "不推荐", "放弃"]
            for c in candidates:
                code = str(c.get("code", ""))
                if not code:
                    continue
                for clause in clauses:
                    if code in clause and any(kw in clause.lower() for kw in reject_kw):
                        rejected.append(code)
                        break

            # Try to extract suggested percentage
            pct_match = _re.search(r"(\d+)[%％]", neutral_text)
            if pct_match:
                suggested_pct = float(pct_match.group(1))

        return {
            "action": action,
            "suggested_total_pct": suggested_pct,
            "rejected_codes": rejected,
            "reasoning": reasoning,
            "debate_trace": "\n".join([
                f"=== AGGRESSIVE ===\n{aggressive_text}",
                f"=== CONSERVATIVE ===\n{conservative_text}",
                f"=== NEUTRAL ===\n{neutral_text}",
            ]),
        }

    def run_merged_stage(self, summaries: dict[str, str]) -> dict:
        """Phase 1-3: Direct API calls with Tool Use for guaranteed structured output."""
        from alphaclaude.tools.llm_client import (
            call_with_tool_safe,
            TOOL_SET_DIRECTION,
            TOOL_ADD_CANDIDATE,
            TOOL_ADJUST_HOLDING,
        )

        # Step 1: Direction + Sectors
        dir_prompt = self._build_direction_prompt(summaries)
        direction = {"bias": "neutral", "confidence": 50, "bias_reasoning": "",
                     "position_cap": None, "preferred": None, "avoid": None}
        tool_inputs = call_with_tool_safe(
            dir_prompt,
            [TOOL_SET_DIRECTION],
            fallback_parser=self._parse_direction_fallback,
        )
        if tool_inputs:
            d = tool_inputs[0]
            bias = str(d.get("bias", "neutral"))
            if bias not in {"bullish", "neutral", "bearish"}:
                bias = "neutral"
            position_cap = self._bounded_int(d.get("position_cap"), 0) or None
            direction = {
                "bias": bias,
                "confidence": self._bounded_int(d.get("confidence"), 50),
                "bias_reasoning": str(d.get("bias_reasoning", "")),
                "position_cap": position_cap,
                "preferred": d.get("prefer_sectors") if isinstance(d.get("prefer_sectors"), list) else None,
                "avoid": d.get("avoid_sectors") if isinstance(d.get("avoid_sectors"), list) else None,
            }

        # Step 2: Candidates (Bull/Bear debate with single-call fallback)
        candidates = []
        debate_trace = ""
        try:
            candidates, debate_trace = self._run_bull_bear_debate(summaries, direction)
        except Exception as exc:
            print(f"[OvernightPipeline] Bull/Bear debate failed: {exc}")

        if not candidates:
            # Fallback to single-call
            candidates_prompt = self._build_candidates_prompt(summaries, direction)
            tool_inputs = call_with_tool_safe(
                candidates_prompt,
                [TOOL_ADD_CANDIDATE],
                fallback_parser=self._parse_candidates_fallback,
            )
            candidates = self._parse_candidates(tool_inputs or [])
            if not candidates:
                if _notify:
                    notify_overnight_timeout(self.run_id,
                        f"选股返回空: {len(tool_inputs or [])}个tool call但无有效candidate")

        # Debug log
        dbg_path = os.path.join(self.output_dir, "_debug_candidates_response.txt")
        with open(dbg_path, "w", encoding="utf-8") as f:
            if debate_trace:
                f.write(f"=== DEBATE TRACE ===\n{debate_trace}\n\n")
            f.write(f"=== FINAL CANDIDATES ===\n{json.dumps(candidates, ensure_ascii=False, indent=2)}\n")

        # Step 3: Adjustments (only if holdings exist)
        adjustments = []
        if self.state.holdings:
            adj_prompt = self._build_adjustments_prompt(direction)
            tool_inputs = call_with_tool_safe(
                adj_prompt,
                [TOOL_ADJUST_HOLDING],
                fallback_parser=self._parse_adjustments_fallback,
            )
            for d in tool_inputs:
                if "code" not in d:
                    continue
                adj = {
                    "code": str(d["code"]),
                    "action": str(d.get("action", "hold")),
                }
                if d.get("reasoning"):
                    adj["reasoning"] = str(d["reasoning"])
                if "new_stop_loss" in d:
                    try:
                        adj["new_stop_loss"] = float(d["new_stop_loss"])
                    except (ValueError, TypeError):
                        pass
                adjustments.append(adj)

        return self._apply_merged(direction, candidates, adjustments)

    def _build_direction_prompt(self, summaries: dict[str, str]) -> str:
        s = self.state.load()
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        parts = [
            f"{sim_date}次日A股方向。请调用 set_direction 工具提交判断。",
            f"宏观: {summaries.get('A','')[:200]}",
            f"板块: {summaries.get('B','')[:200]}",
            f"复盘: {summaries.get('C','')[:200]}",
            self._fetch_market_snapshot(),
            f"账户: 总{self.state.total_value:,.0f} 现金{s['cash']:,.0f}",
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        return "\n".join(parts)

    def _build_candidates_prompt(self, summaries: dict[str, str], direction: dict) -> str:
        sim_date = self.clock.now().strftime("%Y-%m-%d")
        bias = direction.get("bias", "neutral")
        preferred = ",".join(direction.get("preferred") or []) or "无"
        cap = direction.get("position_cap", 80)
        injections = self._get_skill_injections()
        parts = [
            f"{sim_date}次日选股。{bias}偏好{preferred}仓位{cap}%。"
            f"从筛选列表中尽量多选符合条件的标的(目标5-10只), 每只调用一次add_candidate。",
            "仓位分配: 单只5-15%, 多选则分摊。入选理由充分的可给高仓位, 有疑虑的给低仓位。",
            f"板块: {summaries.get('B','')[:200]}",
            self._fetch_candidates_screen(),
            self._bc_rules_text(),
            injections.get("entry_signals", ""),
        ]
        feedback = self._build_shadow_feedback()
        if feedback:
            parts.insert(0, feedback)
        return "\n".join(parts)

    def _build_adjustments_prompt(self, direction: dict) -> str:
        s = self.state.load()
        injections = self._get_skill_injections()
        lines = ["根据持仓调仓。为每只持仓调用一次 adjust_holding 工具。",
                 "如有适合做T的持仓，在reasoning中注明做T方向和触发价位。",
                 "## 持仓"]
        for code, h in s["holdings"].items():
            pnl = (h['current_price'] - h['avg_cost']) / h['avg_cost'] * 100 if h['avg_cost'] > 0 else 0
            lines.append(
                f"  {code}: {h['shares']}股 成本{h['avg_cost']:.2f} "
                f"现价{h['current_price']:.2f} 盈亏{pnl:.1f}% "
                f"止损{h.get('stop_loss','无')}"
            )
        t0 = injections.get("t0_params", "")
        if t0:
            lines.append(t0)
        return "\n".join(lines)


    def _apply_merged(self, direction: dict, candidates: list[dict],
                      adjustments: list[dict]) -> dict:
        """Apply parsed direction, candidates, and adjustments to plan."""
        bias = direction.get("bias", "neutral")
        confidence = direction.get("confidence", 50)
        bias_reasoning = direction.get("bias_reasoning", "")
        position_cap = direction.get("position_cap") or \
            self.variant.get("position_cap_by_bias", {}).get(bias, 50)
        preferred = direction.get("preferred")
        avoid = direction.get("avoid")

        self.plan.set_market_bias(bias, confidence, bias_reasoning, position_cap, preferred, avoid)
        self.plan.set_variant(self.variant)
        self.ledger.append({
            "decision": "overnight_bias", "value": bias,
            "confidence": confidence, "reasoning": bias_reasoning,
            "position_cap": position_cap,
            "variant": self.variant.get("name", "默认"),
        })
        self.plan.set_adjustments(adjustments)
        self.plan.set_candidates(candidates)

        # Apply variant rules to plan
        rules = self.plan.load().get("rules", {})
        rules["max_single_position_pct"] = self.variant.get("max_single_position_pct", 25.0)
        rules["max_total_position_pct"] = self.variant.get("max_total_position_pct", 80.0)
        self.plan.save("variant_rules")

        return {
            "stage": "merged",
            "bias": bias,
            "confidence": confidence,
            "candidates": len(candidates),
            "adjustments": len(adjustments),
        }

    # ── Phase 2: Python Risk Validation ───────────────────────────

    def run_risk_validation(self) -> dict:
        """Python risk.py + signal.py hard validation on merged stage output."""
        from alphaclaude.tools.risk import (
            calc_position_size,
            calc_volatility_adjusted_limit,
            calc_volatility_metrics,
            max_drawdown_check,
        )

        candidates = self.plan._data.get("buy_candidates", [])
        rejected = []
        passed = []
        for c in candidates:
            code = c.get("code", "")
            if not code:
                continue

            # ── Hard checks (must pass) ──
            entry = c.get("entry_max", 0)
            stop = c.get("stop_loss", 0)
            if stop >= entry:
                rejected.append({"code": code, "reason": f"stop {stop} >= entry {entry}", "rule": "signal_hard_check"})
                continue
            if entry > 0 and (entry - stop) / entry < 0.03:
                rejected.append({"code": code, "reason": "risk/reward ratio too low", "rule": "signal_hard_check"})
                continue

            # ── risk.py quantitative checks (non-fatal on error) ──
            try:
                is_bt_risk = self.mode == "backtest" and self.data_feed is not None
                if is_bt_risk:
                    sim_date = self.clock.now()
                    sim_ts = pd.Timestamp(sim_date.strftime("%Y-%m-%d"))
                    df = self.data_feed.get_history_up_to(code, sim_ts)
                else:
                    from alphaclaude.tools._fallback import get_hist
                    df, _ = get_hist(code, days=120)
                if df.empty:
                    rejected.append({"code": code, "reason": "no historical data", "rule": "risk_data"})
                    continue
                closes = [float(x) for x in df["close"].tolist()]
                price = closes[-1]

                # Volatility check
                vol = calc_volatility_metrics(closes)
                annualized_vol = vol["annualized_volatility"]
                limit_pct = calc_volatility_adjusted_limit(annualized_vol)

                # Position sizing — ensure candidate position doesn't exceed vol-adjusted limit
                sizing = calc_position_size(price, self.state.initial_capital, limit_pct)
                sizing_limit_pct = sizing["position_limit_pct"]
                candidate_pct = c.get("position_pct", 20)
                if candidate_pct > sizing_limit_pct:
                    rejected.append({
                        "code": code,
                        "reason": (
                            f"仓位{candidate_pct}%超出波动率调整上限{sizing_limit_pct}% "
                            f"(年化波动率{annualized_vol:.1%})"
                        ),
                        "rule": "risk_volatility",
                    })
                    continue

                # Drawdown check
                dd = max_drawdown_check(closes)
                if dd.get("warn"):
                    rejected.append({
                        "code": code,
                        "reason": (
                            f"个票回撤警告: 当前回撤{dd['current_drawdown_pct']}%，"
                            f"历史最大回撤{dd['max_historical_drawdown_pct']}%"
                        ),
                        "rule": "risk_drawdown",
                    })
                    continue

                # Inject risk-adjusted sizing
                c["position_limit_pct"] = sizing_limit_pct
                c["max_shares"] = sizing["max_shares"]
                c["volatility"] = vol
                passed.append(c)

            except Exception as exc:
                # Non-fatal: let candidate pass through on error
                print(f"[Risk] soft error for {code}: {exc}")
                passed.append(c)

        self.plan._data["buy_candidates"] = passed
        self.plan._data["risk_report"] = {
            "rejected_candidates": rejected,
            "passed_count": len(passed),
            "rejected_count": len(rejected),
        }
        self.plan.save("risk_validation")
        return {"stage": "risk", "passed": len(passed), "rejected": len(rejected)}

    # ── Full Pipeline ─────────────────────────────────────────────

    def run_full(self) -> dict:
        """Phase 0 (parallel sub-agents) → Phase 1 (merged Claude) → Phase 2 (Python risk)."""
        result = {"stages": {}}
        self._record_workflow(
            "success",
            phase="premarket",
            node_id="market_snapshot",
            node_name="市场快照",
            summary="开始盘前计划生成",
            output_refs=["market.snapshot"],
        )

        try:
            summaries = self._run_sub_agents()
            for label, node_id, node_name in (
                ("A", "sub_agent_a", "子代理A: 市场方向"),
                ("B", "sub_agent_b", "子代理B: 选股"),
                ("C", "sub_agent_c", "子代理C: 复盘反馈"),
            ):
                self._record_workflow(
                    "success",
                    phase="premarket",
                    node_id=node_id,
                    node_name=node_name,
                    summary=f"输出 {len(summaries.get(label, ''))} 字符",
                    output_refs=[f"premarket.sub_agent_{label.lower()}"],
                )
        except Exception as exc:
            print(f"[OvernightPipeline] sub-agents failed: {exc}")
            self._record_workflow(
                "error",
                phase="premarket",
                node_id="sub_agent_a",
                node_name="盘前子代理",
                error=str(exc),
            )
            summaries = {"A": "", "B": "", "C": ""}

        # Persist shadow diagnostics if computed
        self._save_shadow_if_dirty(summaries.get("C", ""))

        result["stages"]["merged"] = self.run_merged_stage(summaries)
        self._record_workflow(
            "success",
            phase="premarket",
            node_id="merge_decision",
            node_name="合并决策",
            summary=(
                f"方向 {self.plan._data.get('market_bias', 'unknown')}，"
                f"候选 {len(self.plan._data.get('buy_candidates', []))} 只"
            ),
            input_refs=["premarket.sub_agent_a", "premarket.sub_agent_b", "premarket.sub_agent_c"],
            output_refs=["plan.market_bias", "plan.buy_candidates"],
        )
        result["stages"]["risk"] = self.run_risk_validation()
        self._record_workflow(
            "success",
            phase="premarket",
            node_id="risk_validation",
            node_name="风控校验",
            summary=(
                f"通过 {self.plan._data.get('risk_report', {}).get('passed_count', 0)}，"
                f"拒绝 {self.plan._data.get('risk_report', {}).get('rejected_count', 0)}"
            ),
            input_refs=["plan.buy_candidates"],
            output_refs=["plan.risk_report"],
        )
        self._record_workflow(
            "success",
            phase="premarket",
            node_id="plan_writer",
            node_name="计划写入",
            summary="plan.json 已写入",
            output_refs=["plan.json"],
        )

        # 3.10 Risk debate: triggered only when positions are large
        passed_candidates = self.plan._data.get("buy_candidates", [])
        if passed_candidates:
            debate_result = self._run_risk_debate(passed_candidates)
            if debate_result:
                result["stages"]["risk_debate"] = debate_result

        return result

    def _record_workflow(self, status: str, **kwargs) -> None:
        """Record observability events without affecting trading control flow."""
        try:
            if status == "error":
                self.workflow.record_node_error(**kwargs)
            else:
                self.workflow.record_node_finish(**kwargs)
        except Exception as exc:
            print(f"[Workflow] record failed: {exc}")

    def _save_shadow_if_dirty(self, sub_c_output: str) -> None:
        """Save shadow diagnostics if data was computed during prompt building."""
        if not self._last_shadow_diagnostics:
            return
        try:
            from alphaclaude.tools.shadow_account import load_ledger, pair_trades, compute_diagnostics, save_diagnostics
            entries = load_ledger(self.run_id)
            if entries:
                paired, open_pos = pair_trades(entries)
                diagnostics = compute_diagnostics(paired, open_pos, entries)
                save_diagnostics(self.run_id, diagnostics, sub_c_output)
        except Exception:
            pass

    # ── Emergency Intraday Call ───────────────────────────────────

    def launch_emergency(self, trigger_reason: str, market_data: str = "") -> str | None:
        """Emergency intraday analysis via API + Tool Use for guaranteed structured output.

        Called when market drops >3% or single stock drops >5%.
        """
        s = self.state.load()
        prompt = f"""盘中紧急触发。

## 触发原因
{trigger_reason}

## 市场数据
{market_data}

## 当前持仓
现金: {s['cash']:,.0f}  总资产: {self.state.total_value:,.0f}
"""
        if s["holdings"]:
            for code, h in s["holdings"].items():
                pnl = (h['current_price'] - h['avg_cost']) / h['avg_cost'] * 100 if h['avg_cost'] > 0 else 0
                prompt += f"  {code}: {h['shares']}股 现价{h['current_price']:.2f} 盈亏{pnl:.1f}%\n"
        prompt += '\n请调用 emergency_action 工具提交应急决策。'
        from alphaclaude.tools.llm_client import call_with_tool_safe, TOOL_EMERGENCY_ACTION
        tool_inputs = call_with_tool_safe(
            prompt,
            [TOOL_EMERGENCY_ACTION],
            max_tokens=2048,
            fallback_parser=self._parse_emergency_fallback,
        )
        if tool_inputs:
            self._apply_emergency_decisions(tool_inputs[0])
        return json.dumps(tool_inputs, ensure_ascii=False) if tool_inputs else "(empty)"

    def _apply_emergency_decisions(self, data: dict) -> None:
        """Apply emergency API Tool Use response (already structured JSON)."""
        # Handle stop updates
        for upd in data.get("stop_updates") or []:
            if isinstance(upd, dict) and "code" in upd and "new_stop_loss" in upd:
                try:
                    code = str(upd["code"])
                    new_sl = float(upd["new_stop_loss"])
                    self.plan.update_stop(code, new_sl, updated_by="emergency")
                    self.ledger.append({
                        "decision": "emergency_stop_update",
                        "code": code,
                        "new_stop_loss": new_sl,
                    })
                except (ValueError, TypeError):
                    pass

        # Handle emergency action
        action_type = data.get("action", "hold")
        code_arg = str(data.get("code", ""))
        reasoning = str(data.get("reasoning", ""))

        # Execution logic
        executed = False
        execution_results = []

        def _sellable_shares(holding: dict) -> int:
            shares = int(holding.get("shares", 0) or 0)
            locked = int(holding.get("locked_today", 0) or 0)
            return max(0, shares - locked)

        def _record_execution(result: dict | None) -> None:
            nonlocal executed
            if result:
                execution_results.append(result)
            if isinstance(result, dict) and result.get("status") == "executed":
                executed = True

        if action_type == "close_all" and self.execution:
            for h_code, h in list(self.state.holdings.items()):
                shares = _sellable_shares(h)
                price = h.get("current_price", 0)
                if shares >= 100 and price > 0:
                    result = self.execution.execute_sell(
                        h_code, shares, price,
                        reason=f"emergency_close_all: {reasoning}",
                    )
                    _record_execution(result)
        elif action_type == "reduce" and code_arg and self.execution:
            h = self.state.holdings.get(code_arg, {})
            shares = _sellable_shares(h)
            price = h.get("current_price", 0)
            if shares >= 100 and price > 0:
                reduce_qty = (shares // 200) * 100
                if reduce_qty >= 100:
                    result = self.execution.execute_sell(
                        code_arg, reduce_qty, price,
                        reason=f"emergency_reduce: {reasoning}",
                    )
                    _record_execution(result)
        elif action_type == "close" and code_arg and self.execution:
            h = self.state.holdings.get(code_arg, {})
            shares = _sellable_shares(h)
            price = h.get("current_price", 0)
            if shares >= 100 and price > 0:
                result = self.execution.execute_sell(
                    code_arg, shares, price,
                    reason=f"emergency_close: {reasoning}",
                )
                _record_execution(result)

        entry = {
            "decision": "emergency_action",
            "action": action_type,
            "code": code_arg,
            "reasoning": reasoning,
            "executed": executed,
        }
        if execution_results:
            entry["execution_results"] = execution_results
        self.ledger.append(entry)

# ═══════════════════════════════════════════════════════════════
