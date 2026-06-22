"""Pre-market autonomous Agent workflow and Python risk validation."""

from __future__ import annotations

import json
import os
import re

import pandas as pd

from alphaclaude.config import AGENT_WORKFLOW_TIMEOUT
from alphaclaude.paths import add_legacy_paths
from alphaclaude.engine.agent_task_runner import AgentTaskRunner
from alphaclaude.engine.clock import TradingClock
from alphaclaude.engine.data_feed import BacktestDataFeed
from alphaclaude.engine.execution import ExecutionEngine
from alphaclaude.engine.ledger import Ledger
from alphaclaude.engine.plan import PlanManager
from alphaclaude.engine.state import EngineState
from alphaclaude.engine.workflow_events import WorkflowEventStore

add_legacy_paths()

class OvernightPipeline:
    """After-hours pipeline: autonomous Agent task → Python risk validation.

    The scheduled pre-market path launches a fresh Agent conversation and imports
    its `plan_draft.json`. Python remains responsible for risk gates, execution,
    state, ledger, and emergency handling.
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

    # ── Shared data fetchers ─────────────────────────────────────

    def _fetch_market_snapshot(self) -> str:
        """Fetch market index + north-bound flow data for the Agent task context.

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

    def _build_account_summary(self) -> str:
        try:
            state = self.state.load()
        except Exception:
            state = {"cash": 0, "holdings": {}}
        lines = [
            f"总资产: {self.state.total_value:,.0f}",
            f"现金: {float(state.get('cash', 0) or 0):,.0f}",
        ]
        holdings = state.get("holdings", {}) or {}
        if not holdings:
            lines.append("持仓: 空仓")
            return "\n".join(lines)
        lines.append("持仓:")
        for code, holding in holdings.items():
            avg = float(holding.get("avg_cost", 0) or 0)
            current = float(holding.get("current_price", 0) or 0)
            shares = int(holding.get("shares", 0) or 0)
            pnl = (current - avg) / avg * 100 if avg > 0 else 0
            lines.append(f"  {code}: {shares}股 成本{avg:.2f} 现价{current:.2f} 盈亏{pnl:+.1f}%")
        return "\n".join(lines)

    def _apply_agent_plan_draft(self, draft: dict) -> dict:
        """Import the Agent's plan draft into PlanManager for Python validation."""
        if not isinstance(draft, dict):
            return {"imported": False, "reason": "missing_or_invalid_plan_draft"}

        bias = str(draft.get("market_bias", "neutral"))
        if bias not in {"bullish", "neutral", "bearish"}:
            bias = "neutral"
        confidence = self._bounded_int(draft.get("bias_confidence"), 50)
        position_cap = self._bounded_int(draft.get("position_cap_pct"), 0) or None
        reasoning = str(draft.get("bias_reasoning", draft.get("reasoning", "")))
        preferred = draft.get("preferred_sectors")
        avoid = draft.get("avoid_sectors")
        self.plan.set_market_bias(
            bias,
            confidence,
            reasoning,
            position_cap,
            preferred if isinstance(preferred, list) else None,
            avoid if isinstance(avoid, list) else None,
        )

        candidates = draft.get("buy_candidates", [])
        if isinstance(candidates, list):
            self.plan.set_candidates([c for c in candidates if isinstance(c, dict)])
        else:
            candidates = []

        adjustments = draft.get("holding_adjustments", [])
        if isinstance(adjustments, list):
            self.plan.set_adjustments([a for a in adjustments if isinstance(a, dict)])
        else:
            adjustments = []

        return {
            "imported": True,
            "bias": bias,
            "candidates": len(candidates),
            "adjustments": len(adjustments),
        }

    def _run_agent_premarket_workflow(self, market_snapshot: str) -> dict:
        runner = AgentTaskRunner(
            self.output_dir,
            run_id=self.run_id,
            timeout=AGENT_WORKFLOW_TIMEOUT,
        )
        result = runner.run_premarket_plan(
            market_snapshot=market_snapshot,
            account_summary=self._build_account_summary(),
        )
        draft = result.parsed_artifacts.get("plan_draft")
        imported = self._apply_agent_plan_draft(draft if isinstance(draft, dict) else {})
        return {
            "stage": "agent_research",
            "ok": result.ok,
            "returncode": result.returncode,
            "error": result.error,
            "artifacts_dir": str(result.artifacts_dir),
            "audit_warnings": result.audit_warnings,
            "agent_events": len(result.agent_events),
            "imported": imported,
        }

    def _fetch_candidates_screen(self) -> str:
        """Fetch screen results for local context helpers.

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
                lines.append(f"  共{len(scored)}只通过初筛 (信号评分基于entry-signals):")
                for c in scored:
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
                    for f in fallback:
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

    def _call_text_safe(self, prompt: str, label: str, model: str | None = None) -> str:
        """Call call_text with error handling. Returns '' on failure."""
        from alphaclaude.tools.llm_client import call_text
        try:
            result = call_text(prompt, model=model)
            return (result or "").strip()
        except Exception as exc:
            print(f"[OvernightPipeline] {label} text call failed: {exc}")
            return ""

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

    def _parse_emergency_fallback(self, text: str) -> list[dict]:
        parsed = self._parse_jsonish_tool_text(text)
        if parsed:
            item = dict(parsed[0])
            if item.get("action") not in {"hold", "reduce", "close", "close_all"}:
                item["action"] = "hold"
            item.setdefault("reasoning", "fallback parsed emergency decision")
            return [item]
        return [{"action": "hold", "reasoning": text or "emergency fallback hold"}]

    @staticmethod
    def _bounded_int(value, default: int, low: int = 0, high: int = 100) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, parsed))

    # ── 3.10 Risk Debate ──────────────────────────────────────────────

    def _build_aggressive_risk_prompt(self, candidates: list[dict],
                                       total_pct: float) -> str:
        """Build prompt for the Aggressive risk debater — champions high returns."""
        cand_summary = "\n".join(
            f"- {c['code']}: 仓位{c.get('position_pct',0)}% 入场{c.get('entry_max','?')} "
            f"止损{c.get('stop_loss','?')} 止盈{c.get('take_profit','?')} "
            f"理由:{c.get('reasoning','')}"
            for c in candidates
        )
        return (
            "你是交易风控委员会中的[激进派风控官]。\n"
            "你相信趋势的延续性和波动中的机会，倾向于在可控风险下追求更高收益。\n\n"
            f"当前待执行候选（总仓位{total_pct:.0f}%）：\n{cand_summary}\n\n"
            "请精炼但完整地阐述你的立场，不因字数限制遗漏关键理由：\n"
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
            f"激进派的观点：\n{aggressive_text}\n\n"
            "请精炼但完整地阐述你的立场，不因字数限制遗漏关键理由：\n"
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
            f"=== 激进派 ===\n{aggressive_text}\n\n"
            f"=== 保守派 ===\n{conservative_text}\n\n"
            "请精炼但完整地做出裁决，不因字数限制遗漏关键理由：\n"
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
        reasoning = neutral_text
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

    # ── Phase 2: Python Risk Validation ───────────────────────────

    def run_risk_validation(self) -> dict:
        """Python risk.py + signal.py hard validation on Agent plan draft output."""
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
        """Autonomous Agent research → Python risk validation → final plan."""
        result = {"stages": {}}
        self._record_workflow(
            "running",
            phase="premarket",
            node_id="market_snapshot",
            node_name="市场快照",
            summary="开始盘前计划生成",
            input_refs=["source.quote.market", "source.watchlist", "account.state"],
        )
        market_snapshot = self._fetch_market_snapshot()
        self._record_workflow(
            "success",
            phase="premarket",
            node_id="market_snapshot",
            node_name="市场快照",
            summary="盘前市场快照已生成" if market_snapshot.strip() else "盘前市场快照为空，子代理将标注推断",
            input_refs=["source.quote.market", "source.watchlist", "account.state"],
            output_refs=["artifact.market.snapshot"],
            output_payload={
                "mode": self.mode,
                "time": self.clock.now().isoformat(),
                "market_snapshot": market_snapshot,
            },
        )

        self._record_workflow(
            "running",
            phase="premarket",
            node_id="agent_research",
            node_name="自主 Agent 研究",
            summary="正在启动自主 Agent 盘前研究任务",
            input_refs=["artifact.market.snapshot", "account.state", "rule.skills"],
        )
        result["stages"]["agent_research"] = self._run_agent_premarket_workflow(market_snapshot)
        agent_stage = result["stages"]["agent_research"]
        audit_warning_count = len(agent_stage.get("audit_warnings") or [])
        agent_ok = bool(agent_stage.get("ok"))
        self._record_workflow(
            "warning" if audit_warning_count or not agent_ok else "success",
            phase="premarket",
            node_id="agent_research",
            node_name="自主 Agent 研究",
            summary=(
                f"自主 Agent 盘前研究完成，但审计告警 {audit_warning_count} 条"
                if audit_warning_count
                else "自主 Agent 盘前研究完成"
                if agent_ok
                else "自主 Agent 盘前研究未成功，保留 artifacts 供排查"
            ),
            input_refs=["artifact.market.snapshot", "account.state", "rule.skills"],
            output_refs=["artifact.agent.research", "artifact.agent.plan_draft"],
            output_payload=agent_stage,
        )
        self._record_workflow(
            "running",
            phase="premarket",
            node_id="risk_validation",
            node_name="风控校验",
            summary="正在执行仓位、波动率和回撤校验",
            input_refs=["artifact.agent.plan_draft"],
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
            input_refs=["artifact.agent.plan_draft"],
            output_refs=["plan.risk_report"],
        )
        self._record_workflow(
            "running",
            phase="premarket",
            node_id="plan_writer",
            node_name="计划写入",
            summary="自主 Agent 草案已完成 Python 风控并写入 plan.json",
            input_refs=["artifact.agent.plan_draft", "plan.risk_report"],
            output_refs=["artifact.plan.json"],
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
            if status == "running":
                self.workflow.record_node_start(**kwargs)
            elif status == "error":
                self.workflow.record_node_error(**kwargs)
            elif status == "warning":
                self.workflow.record_node_warning(**kwargs)
            else:
                self.workflow.record_node_finish(**kwargs)
        except Exception as exc:
            print(f"[Workflow] record failed: {exc}")

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
