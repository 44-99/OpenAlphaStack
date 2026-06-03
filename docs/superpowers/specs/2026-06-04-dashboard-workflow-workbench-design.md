# Dashboard 工作流工作台设计

## 背景

AlphaClaude 的 Web 端不应该只是静态行情看板，而应该成为本地运行的“交易软件 + AI IDE 工作台”。面向 A 股用户，核心目标是人机协同：

- 人负责制定规则、审查证据、调整脚手架、给出视觉和效果反馈。
- Agent 和 Python 工具负责生成计划、扫描信号、校验风控、执行模拟交易、沉淀审计记录。
- 每一个重要决策都应该能在 Dashboard 上看到来源、过程、输入、输出和最终影响。

当前 Dashboard 已有基础能力：

- React/Vite 前端。
- K 线图：周期切换、MA/EMA/BOLL、VOL、tooltip、缩放和平移。
- 模拟盘状态、计划、账本、自选股、缓存状态和 SSE。
- 右侧 Agent 终端，可在浏览器内启动 Claude Code / Codex CLI。

下一步不是继续堆指标，而是把“盘前计划 -> 盘中决策 -> 执行结果 -> 盘后复盘”的完整链路做成可视化、可追踪、可配置的工作台。

## 产品结构

Dashboard 顶部提供三个一级工作区：

```text
[盯盘] [流程] [复盘]
```

### 盯盘模式

默认入口，交易软件优先。K 线图是主画布。

它回答的问题是：

> 当前 AI / Python 系统做了什么？风险线在哪里？我现在要不要干预？

核心能力：

- K 线图层系统。
- 交易结果可视化。
- 计划和信号叠加。
- 盘中事件和预警。
- 图表标注可跳转到对应流程节点。
- 右侧 Agent 终端持续可用，方便边盯盘边讨论和干预。

### 流程模式

审计和编排入口，体验上更接近 Dify / LangGraph，而不是普通日志列表。

它回答的问题是：

> Agent 流程运行到哪一步？输入是什么？输出是什么？下一步流向哪里？结果是否可信？

核心能力：

- 工作流 DAG 画布。
- 节点状态可视化。
- 节点 Inspector，展示输入、输出、artifact 和错误。
- 事件时间线，用于审计细节。
- 节点参数查看，后续支持安全修改。

### 复盘模式

第一版不单独做复杂复盘系统，而是基于同一套工作流事件数据做轻量视图。

它回答的问题是：

> 今天计划和实际执行偏差在哪里？哪些决策值得保留，哪些规则需要修改？

初始卡片：

- 今日计划 vs 实际执行。
- 交易结果与风控事件。
- Agent / Shadow Account 反思摘要。

每个复盘项都应能跳回对应流程节点或 K 线标注。

## K 线图层系统

K 线图不能默认把所有信号都画上去，否则会变乱。第一版使用明确的图层开关：

```text
图层
✓ 交易结果
□ 计划执行
□ 技术信号
□ 高级结构
```

### 交易结果层

默认开启。

显示内容：

- 买入点。
- 卖出点。
- 持仓成本线。
- 止损线。
- 止盈线。
- 当前浮盈亏标签。

目的：

> 让用户立刻看懂 AI / Python 实际做了什么，以及当前风险边界在哪里。

### 计划执行层

默认关闭。

显示内容：

- 计划入场区间。
- 候选有效期。
- 计划仓位。
- 触发 / 未触发原因。
- 风控拒绝原因。

目的：

> 对比原计划和实际执行，解释为什么执行或没有执行。

### 技术信号层

默认关闭。

第一批信号：

- MA 金叉 / 死叉。
- 放量突破。
- 回踩 MA5 / MA10。
- BOLL 上下轨触碰。
- 支撑 / 阻力。

目的：

> 让用户看到 Agent 或规则当时依据了哪些技术信号。

### 高级结构层

第一版只预留接口，后续再接入结构化 skill 输出。

候选内容：

- 缠论中枢。
- 箱体。
- 波浪段落。
- 趋势线。
- Skill 生成的高级结构标注。

高级结构层只有在上游 skill 输出可结构化、可审计时才实现。复杂主观画线必须可开关，并且要能追溯来源。

### 标注 Inspector

点击 K 线上的标注时，显示来源链路：

```text
买入 300077
时间: 2026-06-03 09:44:59
价格: 25.79
来源: plan.buy_candidates -> risk_validation -> fastlane.tick -> ledger write
理由: 半导体/芯片板块核心标的...
风控: passed, rejected: 600198 drawdown
```

标注应能跳转到对应流程节点。

## 工作流模型

第一版使用“工作流事件总线”，不立刻重写成完整 graph runtime。

原因：

- 当前引擎已经可运行。
- 全量 graph runtime 重构风险高，容易影响模拟盘稳定性。
- 事件埋点可以先获得可追踪能力，并为未来升级 graph runtime 留出路径。

### 内置工作流模板

```text
盘前链:
Market Snapshot -> Sub-Agent A/B/C -> Merge Decision -> Bull/Bear Debate -> Risk Validation -> Plan Writer

盘中链:
State Watcher -> FastLane Tick -> Signal Scan -> Execution Check -> Order Simulator -> Ledger/State Writer -> Alert Router

盘后链:
Daily Report -> Ledger Pairing -> Shadow Account -> Mistake Pattern -> Agent Reflection -> Next Plan Memory
```

流程画布按这个模板展示 DAG。第一版不支持自由拖线改依赖。

## 事件粒度

采用中粒度事件。

记录：

- Agent 节点。
- Tool 节点。
- Skill 级决策节点。
- 风控校验。
- 信号扫描。
- 执行和账本写入摘要。
- 配置变更。

不在事件行里记录每一行内部日志、每次 quote 请求、每只股票每条规则的全部细节。大内容放到 artifact 文件。

### 事件格式

`workflow_events.jsonl` 每行是一个 JSON 事件：

```json
{
  "event_id": "wf_20260604_093001_xxx",
  "run_id": "paper_...",
  "phase": "premarket",
  "node_id": "risk_validation",
  "node_name": "风控校验",
  "status": "success",
  "started_at": "2026-06-04T09:30:01",
  "ended_at": "2026-06-04T09:30:03",
  "duration_ms": 2031,
  "input_refs": ["plan.buy_candidates", "state.cash"],
  "output_refs": ["plan.risk_report"],
  "summary": "3 candidates, 2 passed, 1 rejected",
  "error": "",
  "artifact_dir": "workflow_artifacts/wf_20260604_093001_xxx"
}
```

### Artifact 目录

```text
data/output/<run_id>/
  workflow_events.jsonl
  workflow_config.json
  workflow_artifacts/
    <event_id>/
      input.json
      output.json
      prompt.txt
      response.txt
      error.txt
```

规则：

- 事件行保持短小可读。
- 完整 prompt、response、tool payload、错误栈放到 artifact。
- artifact 通过 `event_id` 引用。
- `plan.json`、`state.json`、`ledger.jsonl` 继续作为交易事实来源。
- 工作流事件只解释这些事实是如何产生的。

## 工作流配置

第一版支持“安全模板化编排”。

用户可以：

- 启用 / 禁用允许修改的节点。
- 修改允许修改的节点参数。
- 重跑安全节点。
- 把节点上下文发送给右侧 Agent 终端讨论。

用户不可以：

- 自由拖线连接任意节点。
- 移除执行链路中的所有风控校验。
- 重跑订单或账本写入节点导致重复交易。

示例 `workflow_config.json`：

```json
{
  "version": 1,
  "nodes": {
    "bull_bear_debate": {
      "enabled": true,
      "params": {
        "max_rounds": 1,
        "candidate_limit": 5
      }
    },
    "fastlane_signal_scan": {
      "enabled": true,
      "params": {
        "allow_new_positions": true,
        "tick_interval_sec": 1
      }
    },
    "risk_validation": {
      "enabled": true,
      "locked": true,
      "params": {
        "max_single_position_pct": 25,
        "max_total_position_pct": 80
      }
    }
  }
}
```

## 后端 API

新增 Dashboard Workflow API：

```text
GET  /api/workflow/runs/{run_id}/events
GET  /api/workflow/runs/{run_id}/graph
GET  /api/workflow/runs/{run_id}/config
POST /api/workflow/runs/{run_id}/config
POST /api/workflow/runs/{run_id}/nodes/{node_id}/rerun
GET  /api/workflow/runs/{run_id}/artifacts/{event_id}/{name}
```

扩展 SSE 事件类型：

```text
workflow_event
workflow_config_updated
workflow_node_started
workflow_node_finished
```

新增后端模块：

```text
src/alphaclaude/engine/workflow_events.py
```

职责：

- 追加写入 `workflow_events.jsonl`。
- 创建 `workflow_artifacts/<event_id>/`。
- 提供 `record_node_start`、`record_node_finish`、`record_node_error`、`record_node_skip`。
- 提供 `record_config_change`。
- 读取事件并聚合当前 DAG 节点状态。

## 引擎接入点

### OvernightPipeline

- Market Snapshot。
- Sub-Agent A。
- Sub-Agent B。
- Sub-Agent C。
- Merge Decision。
- Bull/Bear Debate。
- Candidate Screen。
- Risk Validation。
- Plan Writer。

### PaperEngine

- 阶段切换。
- 盘中补跑计划恢复。
- 盘后报告。
- 观察模式状态变化。

### FastLane

- FastLane Tick。
- Signal Scan。
- Holding Adjustment。
- Execution Check。
- Order Simulator。
- Ledger Writer。
- Alert Router。

工作流事件层只负责记录，不应成为交易执行的必要条件。

## 安全边界

- `risk_validation` 可暴露参数，但不能从执行链路中移除。
- live 模式必须比 paper 模式更严格。
- 未来 live 模式中，任何影响下单的配置变更都必须进入 `pending_confirmation`。
- paper 模式可以立即应用用户确认的配置变更，但必须写审计事件。
- 配置变更只影响未来 tick 或未来节点运行，不改写历史订单。
- 第一版只允许重跑盘前和盘后节点。
- 盘中执行节点不能随意重跑，否则可能导致重复下单。
- `Ledger Writer` 和 `Order Simulator` 在支持重跑前必须先有幂等保护。

## 错误处理

- 失败节点在流程画布中显示红色。
- Inspector 显示错误摘要并链接到 `error.txt`。
- Workflow API 错误不能导致 Dashboard 崩溃。
- 如果 `workflow_events.jsonl` 部分损坏，API 返回可读诊断，并保留原始文件路径。
- 大 artifact 先显示摘要，用户按需打开完整内容。

## 实施阶段

### Phase A: 工作流可观测

目标：先让流程看得见。

交付：

- `workflow_events.py`。
- 写入 `workflow_events.jsonl`。
- 盘前链埋点。
- 最小盘中埋点。
- events、graph、artifacts API。
- 流程模式 DAG 画布。
- 节点 Inspector。

Phase A 不做参数编辑，不做节点重跑。

验收：

- 盘前 pipeline 卡住时，Dashboard 能显示当前卡在哪个节点。
- 成功节点显示输入、输出、耗时和摘要。
- 失败节点显示错误。
- 不影响现有 paper engine 正常运行。

### Phase B: K 线 AI 图层

目标：把 AI 决策画到 K 线上。

优先交付：

- 交易结果层。
- 买入 / 卖出标记。
- 成本线。
- 止损线和止盈线。
- 浮盈亏标签。
- 标注 Inspector。

后续交付：

- 计划执行层。
- 技术信号层。

验收：

- 用户能从 K 线直接看到 Agent / Python 系统实际做了什么。
- 默认图表保持可读。
- 标注来源链路能跳回工作流事件。

### Phase C: 安全模板化编排

目标：让用户开始调整 Agent 脚手架。

交付：

- `workflow_config.json`。
- Config API。
- 节点参数面板。
- 启用 / 禁用允许修改的节点。
- 配置变更审计事件。
- 盘前 / 盘后节点安全重跑。
- 节点上下文发送给 Agent 终端。

验收：

- 用户能调整非关键节点和参数。
- 风控节点不能被完全绕过。
- 所有配置变更可追踪。
- 盘中动作不会因为重跑而重复下单。

## 第一版范围

推荐第一版只做：

1. Phase A 完整。
2. Phase B 的交易结果层。

暂不做：

- 完整自由拖拽 graph runtime。
- 缠论 / 波浪理论渲染。
- live 交易确认流。
- 任意盘中节点重跑。

这样第一版能直接解决两个核心痛点：

- 用户能看到 Agent 流程卡在哪里、输出了什么。
- 用户能在 K 线图上看到计划和执行结果。

同时不会过度重构当前模拟盘。

## 后续实施时需要确定的工程细节

- DAG 渲染库选择。
- artifact 文件大小限制。
- `workflow_config.json` schema 版本策略。
- 事件时间线在 UI 中的具体位置。

