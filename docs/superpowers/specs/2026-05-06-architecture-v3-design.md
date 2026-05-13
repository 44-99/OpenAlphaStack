# Architecture v3 — Mixed Alpha, Sub-Agent Research, High-Frequency Tick

**Date:** 2026-05-06
**Status:** design review

> 2026-05-13 注：本文是 v3 设计记录；路径已按当前包结构更新为 `src/alphaclaude/`。旧根入口、旧工具目录和旧单文件引擎入口已迁移/删除。

## Summary

v2 暴露了四个根因问题（死叉买入、重复下单、仓位失控、Stage 2 不工作）。v3 在此基础上做三件事：修 bug、加速、引入 sub-agent 研究层。

## Architecture Diagram

```
┌─ 盘前 (8:00-9:15) ─ 每天 1 次 ─────────────────────────────────┐
│                                                                    │
│  Phase 0: Python 并行启动 3 个 Claude Code 子任务 (sub-agent)       │
│  ┌─ A: 宏观政策研究 → 500 字摘要 (Tavily 搜索+解读)                │
│  ├─ B: 板块轮动分析 → 推荐 3 个板块 + 理由 (~500 字)              │
│  └─ C: 决策复盘 + 持仓评估 → 经验注入 (~500 字)                   │
│                                                                    │
│  Phase 1: 合并决策 (拿到 A+B+C 摘要 → Python 注入 prompt)          │
│  Stage 1: Claude Code 定方向 + 选标的 + 持仓调整 (单一聚焦调用)    │
│  输入: 3 摘要 + 行情 + screen 20只 + 账户状态                      │
│  输出: bias + candidates + adjustments                             │
│                                                                    │
│  Phase 2: 纯 Python 风控                                           │
│  risk.py 校验 + signal.py 验证 → plan.json                         │
│                                                                    │
└──────────────────────────┬─────────────────────────────────────────┘
                           ▼ plan.json
┌─ 盘中 (9:25-15:00) ─ Python 机械执行 ─────────────────────────┐
│                                                                    │
│  · 9:25  执行候选买入 (限价单, 不追高)                              │
│  · 每 1s: 止盈止损检查 (并行行情 + 并行扫描)                        │
│  · 每 1s: 规则信号扫描 (action=buy/sell/alert)                     │
│  · 紧急: 大盘-3% / 个票-5% / 账户-10% → Claude Code 紧急会话       │
│                                                                    │
└──────────────────────────┬─────────────────────────────────────────┘
                           ▼
┌─ 全局风控 (硬编码, 不可跳过) ──────────────────────────────────┐
│                                                                    │
│  · 单笔: 卫星 -5% / 核心 -8% 硬止损                                 │
│  · 账户: -20% → 停止一切交易, 推送报警                              │
│  · 仓位: 核心 ≤50% + 卫星 ≤30% = 总 ≤80% (回测/模拟盘可全仓)       │
│  · 单票: 核心 ≤20% / 卫星 ≤7.5%                                    │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. Sub-Agent 研究层 (Phase 0)

**问题:** 单个 Claude Code 上下文要同时处理宏观研究、板块轮动、个股精选、持仓调整，prompt 过长导致注意力分散。

**方案:** Python 在 Stage 1 之前并行启动 3 个聚焦的 `claude -p` 子任务。每个 prompt ≤500 字，返回 ≤300 字摘要。主 Stage 只读摘要，不看原始数据。

**日调用量:** 3 子 Agent (并行, `claude -p`) + 1 合并 Stage (`claude -p`) = 共 4 次。子 Agent 并行执行，实际串行等待约 2 次调用。Stage 合并后 Claude 能在同一上下文中看到方向+标的，判断更连贯。单次成本极低（-p 模式无工具，token 少）。

**降级策略:** 如果子任务超时/失败，Stage 仍可运行（用空摘要 + "数据不可用"标记）。不阻塞主流程。

### 2. 1s Tick (并行行情 + 并行扫描)

**实测数据:** 并行 10 股行情 40ms + 并行 10 股扫描 34ms = 完整 tick 124ms。稳定 6 tick/s，留 30% 余量 4 tick/s。

**实现:**
- `ThreadPoolExecutor(max_workers=10)` 并行拉取行情
- `ThreadPoolExecutor` 并行运行 `scan_code`
- 默认 tick=1s，可配置 `--tick-interval 0.5`

**为什么 1s:** 免费 API 行情刷新间隔 3-5s，1s 已经过采样。500ms 也无妨但没实际收益。

### 3. 规则信号 Action 字段

**问题:** `signal_rules.py` 只输出规则名称，不输出买卖方向。FastLane 对所有信号无条件执行买入。

**修复:**
| 规则 | action | 说明 |
|------|--------|------|
| ma_golden_cross | buy | 金叉看涨 |
| ma_death_cross | sell | 死叉看跌 |
| volume_breakout | buy | 放量突破 |
| deviation_alert (>5%) | alert | 乖离过大，不买 |
| deviation_alert (<-5%) | alert | 超跌预警 |
| alignment_turn_bullish | buy | 多头排列形成 |
| alignment_turn_bearish | sell | 空头排列形成 |
| gap_alert (>3%) | alert | 缺口预警 |
| gap_alert (<-3%) | alert | 缺口预警 |

**FastLane 处理:**
- `buy` → 未持仓 → 卫星买入 (≤7.5%)，含去重逻辑
- `sell` → 已持仓 → 全部卖出
- `alert` → 写入 event_queue，不执行交易

**去重:** 同一 (code, rule) 24h 内不重复触发。同一 code 一天最多 1 笔规则信号交易。

### 4. 回测/模拟盘中仓位上限

回测和模拟盘不设仓位硬上限（可全仓），目的是暴露策略在极端情况下的真实表现。实盘再加保守限制。

### 5. 账户级熔断

`max_drawdown_check()` 在每笔交易前执行：当前回撤 ≥20% → 拒绝所有新开仓，推送飞书报警。仅允许平仓（止损/止盈）。

## Changed Files

| File | Change |
|------|--------|
| `src/alphaclaude/tools/signal_rules.py` | 每个规则输出加 `action` 字段 (buy/sell/alert) |
| `src/alphaclaude/engine/` | FastLane 并行行情+扫描，1s tick，action 分支，去重，熔断；OvernightPipeline Phase 0 子任务 |
| `docs/roadmap.md` | Phase 2 架构描述更新为 v3 |

## Unchanged Files

`src/alphaclaude/tools/risk.py`, `signal.py`, `screen.py`, `_fallback.py`, `quote.py` — 直接复用，无需修改。

## Implementation Order

1. `src/alphaclaude/tools/signal_rules.py` action 字段 (独立, 30min)
2. `src/alphaclaude/engine/fast_lane.py` FastLane 并行 + action 处理 + 去重 (依赖 1, 2h)
3. `src/alphaclaude/engine/` 全局熔断 (独立, 30min)
4. `src/alphaclaude/engine/pipeline.py` Phase 0 sub-agents + 合并 Stage 1 (独立, 1.5h)
5. Dry-run 回测验证 (全仓, 1 个月)
6. Full backtest (含 Claude Code, 1 个月)
7. `docs/roadmap.md` 更新
