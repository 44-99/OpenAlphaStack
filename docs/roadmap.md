# 路线图

路线图只记录优先级、完成状态和下一步交付边界。架构图集中维护在 [architecture.md](architecture.md)，项目对比和外部启发集中维护在 [project-comparison.md](project-comparison.md)。

## 当前进度

| 范围 | 状态 | 说明 |
|------|------|------|
| 包结构重构 | ✅ 完成 | 应用入口、引擎核心、CLI 工具已迁入 `src/alphaclaude/`；旧根入口和旧 `tools/` 目录不保留兼容残留 |
| 工具底座 | ✅ 完成 | 行情、技术、基本面、资金、信号、风控、报表等工具改为 `python -m alphaclaude.tools.<tool>` |
| 引擎核心 | ✅ 完成 | `state`、`plan`、`ledger`、`execution`、`fast_lane`、`pipeline`、`paper` 等模块已拆分 |
| 回测/模拟盘 | ✅ 可用 | 共享 `alphaclaude.engine` 包内核心；测试覆盖关键止盈止损、T+0、事件队列、数据源等行为 |
| `live` 模式 | ⛔ 未准入 | CLI 入口预留，但缺 BrokerAdapter、订单确认、订单幂等、实盘安全闸门 |
| API 可靠性 | ✅ 完成 | `call_with_tool_safe()` 已接入 OvernightPipeline 关键结构化调用；Tool Use 失败时走文本 fallback 或保守空结果 |

## Phase 1: 工具底座 ✅ 完成

目标：让 Claude Code 有稳定、可重复调用的数据和计算工具。

已交付：

| 类别 | 包内模块 |
|------|----------|
| 行情/技术 | `quote`、`technical`、`trend`、`pivot`、`fibonacci`、`sentiment` |
| 基本面/资金/消息 | `fundamental`、`flow`、`news` |
| 筛选/回测 | `screen`、`backtest`、`backtest_runner` |
| 信号/风控 | `signal`、`signal_rules`、`signal_detector`、`risk` |
| 运维/报表 | `engine_status`、`daily_report`、`notifier`、`portfolio` |

保留原则：

- 工具保持无状态，JSON 进 JSON 出。
- 数据源继续使用腾讯 → 新浪 → akshare fallback。
- 新工具必须放入 `src/alphaclaude/tools/`，不再恢复根目录 `tools/`。

## Phase 2: 策略引擎 🔄 主体完成

目标：让回测和模拟盘在同一套引擎核心上运行，并具备可验证的策略闭环。

已完成：

| 项 | 状态 | 说明 |
|----|------|------|
| 引擎模块化 | ✅ | 单文件引擎拆为 `alphaclaude.engine.*` 模块 |
| 包入口 | ✅ | `alphaclaude-engine` 和 `python -m alphaclaude.engine.cli` |
| 盘中快车道 | ✅ | `FastLane`、事件队列、止盈止损、T+0 支持 |
| 状态/计划/账本 | ✅ | `EngineState`、`PlanV2`、`Ledger` 独立模块 |
| 回测数据适配 | ✅ | `BacktestDataFeed` 和交易日/分钟线生成拆分 |
| Shadow Account Phase A | ✅ | 从 `ledger.jsonl` 做交易配对和行为诊断 |
| Bull/Bear 选股辩论 | ✅ | `OvernightPipeline` 已有牛熊/风控裁决编排 |
| 策略变体 | ✅ | `strategy_variants` 和 plan 中的 variant 参数已接入 |
| 结构化输出降级 | ✅ | direction、候选裁决、候选 fallback、持仓调整、emergency action 已改用 `call_with_tool_safe()` |
| Shadow Account Phase B | ✅ | 最新 shadow diagnostics 可生成 2-4 句复盘反思，并注入 Sub-Agent C prompt |
| 盘前/盘中/盘后节奏 | ✅ | 盘前 Claude Code 生成 plan，盘中 Python 机械执行，盘后只做 Python 报告 |
| 运行控制基础 | ✅ | `--daemon` 脱离长进程避免工具会话挂起，`--stop-running` 基于 PID metadata 停止已记录引擎 |
| 测试替代旧脚本 | ✅ | 删除长时间挂起的旧 `test_ops.py`，改为 pytest 覆盖 |

剩余工作：

| 优先级 | 项 | 来源 | 完工标准 |
|--------|----|------|----------|
| P1 | Shadow Account Phase B 测试加固 | TradingAgents | 增加离线样本测试，覆盖 diagnostics 生成、reflection 保存、prompt 注入 |
| P1 | 双模型分层 | TradingAgents | 研究/辩论使用便宜模型，最终结构化决策使用主模型，配置可通过 `.env` 切换 |
| P1 | 工具输出压缩 | Vibe-Trading | 常用工具组合输出能压缩为更短摘要，减少 Claude Code 上下文占用 |

## Phase 3: 实盘准入 ⛔ 未开始

目标：真实资金交易前，先把订单边界、人工确认和熔断能力补齐。Phase 3 未完成前，`--mode live` 不允许视为真实交易能力。

必须完成：

| 优先级 | 项 | 说明 |
|--------|----|------|
| P0 | BrokerAdapter | 在 `alphaclaude.engine` 内定义券商适配接口，隔离模拟成交和真实下单 |
| P0 | PAPER_ONLY 安全闸门 | 默认禁止实盘；需要 `.env` 和运行时指令双重确认 |
| P0 | 订单幂等 | 每笔订单有唯一 `trade_id`，重启/重试不能重复下单 |
| P0 | 人工确认流 | 大额或所有实盘订单必须走飞书确认卡片 |
| P0 | 运行控制面 | 支持按 run_id 暂停、恢复、停止和查询，不再依赖“最新进程”推断 |
| P1 | 多持仓相关性风控 | 高相关持仓自动下调仓位上限 |
| P1 | 大额交易风控辩论 | 借鉴 TradingAgents 的 Aggressive/Conservative/Neutral 三方风控审查 |

准入门槛：

- 模拟盘连续运行不少于 1 个月。
- 至少 30 笔有效交易。
- 胜率、回撤、夏普和异常恢复能力达到人工确认标准。
- 实盘前必须完成一次小资金 dry-run 和断线/重启演练。

## Phase 4: 交互和运维增强

目标：不改变交易核心，改善使用体验、可观测性和维护成本。

| 优先级 | 项 | 来源 | 说明 |
|--------|----|------|------|
| P0 | 流式消息回复 | cc-connect | Claude Code `stream-json` 逐段推送到飞书，减少长时间空白等待 |
| P0 | Session 自动轮转 | cc-connect | 防止长会话上下文膨胀，支持整理记忆后重置 |
| P1 | 回复冗长度模式 | cc-connect | `/mode full/compact/quiet` 控制手机端信息密度 |
| P1 | 飞书富卡片 | cc-connect | 用按钮、表格和确认卡片承载交易确认/状态摘要 |
| P1 | 轻量监控面板 | cc-connect / FinceptTerminal | 展示 run、净值、持仓、交易和错误 |
| P2 | 工具注册元数据 | Vibe-Trading | 为包内工具添加 metadata，自动生成 CLAUDE.md 工具表 |

## 暂不做

| 项 | 原因 |
|----|------|
| MCP Server 包装 | 当前 Claude Code 直接调用包内 CLI 已足够；不是赚钱或安全瓶颈 |
| 大前端重构 | 当前核心风险在引擎和实盘边界，不在 UI |
| 跨市场泛化 | 项目优势是 A 股 T+1、政策市和本地工具链，不应过早稀释 |
| LangGraph 全量迁移 | 当前引擎循环简单可测；等 Agent 协作复杂度明显上升再评估 |

## 验证基线

每轮引擎或文档入口变更后至少运行：

```powershell
python -m pytest -q
python -m compileall -q src\alphaclaude
$env:PYTHONPATH='src'; python -m alphaclaude.engine.cli --help
$env:PYTHONPATH='src'; python -m alphaclaude.tools.quote --help
```
