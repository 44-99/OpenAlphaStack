# 路线图

路线图只记录优先级、完成状态和下一步交付边界。架构图集中维护在 [architecture.md](architecture.md)，项目对比和外部启发集中维护在 [project-comparison.md](project-comparison.md)。

## 当前进度

| 范围 | 状态 | 说明 |
|------|------|------|
| 包结构重构 | ✅ 完成 | 应用入口、引擎核心、CLI 工具和飞书适配已迁入 `src/alphaclaude/` |
| 工具底座 | ✅ 完成 | 行情、技术、基本面、资金、信号、风控、报表等工具改为 `alphaclaude tools <tool>` |
| 引擎核心 | ✅ 完成 | `state`、`plan`、`ledger`、`execution`、`fast_lane`、`pipeline`、`paper` 等模块已拆分 |
| 回测/模拟盘 | ✅ 可用 | 共享 `alphaclaude.engine` 包内核心；支持 daemon 启动、run 查询、停止和恢复 |
| Dashboard React 迁移 | ✅ 基础完成 | 旧单文件 Dashboard 已迁为 React + Vite + TypeScript，由 FastAPI 服务构建产物 |
| K 线交互 | ✅ 基础完成 | 日/周/分钟周期、MA/EMA/BOLL、VOL、tooltip、拖拽、slider、低灵敏度滚轮缩放已恢复 |
| Agent 工作台 | ✅ 基础完成 | 右侧 xterm 终端通过 WebSocket 连接本机 PowerShell，可切换 Claude Code / Codex CLI |
| K 线缓存管理 | ✅ 基础完成 | 支持缓存大小显示和手动清理所有 Dashboard K 线缓存层级 |
| 飞书通道 | ✅ 可用 | 中文菜单指令、状态/持仓/交易/计划/停止/恢复、关键告警推送 |
| 对外展示 | 🔄 进行中 | README 已加入 Dashboard 截图、目标用户和最短体验路径；仍缺 GIF/视频和稳定 demo 数据 |
| `live` 模式 | ⛔ 未准入 | CLI 入口预留，但缺 BrokerAdapter、订单确认、订单幂等、实盘安全闸门 |

## Phase 1: 工具底座 ✅ 完成

目标：让 Claude Code / Codex 有稳定、可重复调用的数据和计算工具。

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

## Phase 2: 策略引擎 ✅ 主体完成

目标：让回测和模拟盘在同一套引擎核心上运行，并具备可验证的策略闭环。

已完成：

| 项 | 状态 | 说明 |
|----|------|------|
| 引擎模块化 | ✅ | 单文件引擎拆为 `alphaclaude.engine.*` 模块 |
| 包入口 | ✅ | 统一为 `alphaclaude app/engine/tools ...` 子命令结构 |
| 盘中快车道 | ✅ | `FastLane`、事件队列、止盈止损、T+0 支持 |
| 状态/计划/账本 | ✅ | `EngineState`、`PlanV2`、`Ledger` 独立模块 |
| 回测数据适配 | ✅ | `BacktestDataFeed` 和交易日/分钟线生成拆分 |
| Shadow Account | ✅ | 可从 `ledger.jsonl` 做交易配对、行为诊断和复盘反思 |
| Bull/Bear 选股辩论 | ✅ | `OvernightPipeline` 已有牛熊/风控裁决编排 |
| 策略变体 | ✅ | `strategy_variants` 和 plan 中的 variant 参数已接入 |
| 结构化输出降级 | ✅ | 关键 Tool Use 路径已接入 `call_with_tool_safe()` |
| 盘前/盘中/盘后节奏 | ✅ | 盘前 Agent 生成 plan，盘中 Python 机械执行，盘后 Python 报告 |
| 运行控制基础 | ✅ | `start --daemon`、`list/status/stop/resume/stop-running` 可用 |

下一步不是继续堆策略，而是提高模拟盘稳定运行样本量和 Dashboard 可操作性。

## Phase 3: Dashboard 工作台 🔄 进行中

目标：把 Dashboard 做成真正可用的交易软件 + AI IDE 工作台，而不是静态监控页。

已完成：

| 项 | 状态 | 说明 |
|----|------|------|
| React/Vite 迁移 | ✅ | Dashboard 源码位于 `dashboard/`，FastAPI 服务 `dashboard/dist` |
| 单命令开发启动 | ✅ | `npm run dev` 同时启动后端和前端热重载，并自动清理端口 |
| K 线组件隔离 | ✅ | ECharts 生命周期由 `KlineChart` 管理，option 构造可测试 |
| K 线交互恢复 | ✅ | 十字光标、tooltip、拖拽、slider、滚轮缩放已恢复 |
| 触控板灵敏度 | ✅ | 自定义 wheel handler 降低触控板缩放过快问题 |
| 暗色金融 tooltip | ✅ | 替换 ECharts 默认白底 tooltip，只在 K 线 item 命中时显示 |
| 分钟周期链路 | ✅ | `1m/5m/15m/60m` 通过后端缓存和 resample 返回 |
| K 线缓存 UI | ✅ | 顶部显示缓存大小，按钮手动清理所有 K 线缓存层级 |
| 左侧可折叠/拖宽 | ✅ | 真实图标，展开/折叠居中显示 |
| 右侧 Agent 终端 | ✅ | xterm + WebSocket + winpty + PowerShell，支持 Claude/Codex 切换 |

近期优先级：

| 优先级 | 项 | 状态 | 说明 |
|--------|----|------|------|
| P0 | Agent 终端稳定性验证 | 待持续验证 | 重点检查 Claude/Codex CLI 在 xterm 中的交互、窗口尺寸、退出和重连 |
| P0 | Dashboard 构建体积拆分 | 待做 | 当前 ECharts 使 JS chunk 偏大，可考虑动态 import chart/terminal |
| P1 | 盘中事件流 | 待做 | 不写死阈值，优先呈现成交、拒单、止损、计划偏离和用户确认动作 |
| P1 | 股票上下文操作 | 待设计 | 基于当前选中股票和当前缩放视野，而不是固定最近 N 根 K 线 |
| P1 | Plan 热更新 UI | 待设计 | 终端 Agent 给建议，用户确认后再更新 plan，避免 Agent 直接改交易计划 |
| P2 | 指标副图扩展 | 待做 | MACD/RSI/KDJ 可恢复，但应避免与 K 线主交互冲突 |

## Phase 4: 飞书与通知 ✅ 基础完成

目标：飞书作为手机端降级入口和关键告警渠道，而不是替代 Dashboard。

已完成：

- 飞书 WebSocket 长连接。
- 中文菜单指令：`状态/持仓/交易/计划/停止/恢复/帮助`。
- 流式消息回复。
- Session 自动轮转。
- 模拟盘运行状态、持仓、成交和计划查询。

继续保持：

- 关键告警推送到飞书。
- 深度分析和盘中调整优先在 Dashboard 右侧 Agent 终端完成。
- 不恢复复杂卡片确认方案；实盘确认未来优先使用简单明确的纯文本二次确认。

## Phase 5: 对外展示和采用 🔄 进行中

目标：解决“项目没人看没人关注”的实际问题，让陌生人能在 10 秒内理解项目价值，在 5 分钟内判断是否值得继续安装。

已完成：

- README 首屏加入 Dashboard 工作台截图。
- README 明确项目不是自动赚钱机器人，而是本地 AI Agent 交易工作台。
- README 明确目标用户：A 股散户程序员、Claude Code/Codex 用户、量化和交易系统学习者。
- README 加入最短体验路径，降低飞书配置带来的初始心理门槛。

下一步：

| 优先级 | 项 | 说明 |
|--------|----|------|
| P0 | README GIF | 展示 K 线缩放、tooltip、周期切换和右侧 Agent 终端输入输出 |
| P0 | Demo 数据模式 | 提供不用飞书、不用真实模拟盘也能看 Dashboard 的本地示例数据 |
| P1 | 运行故事 | 写一段盘前计划、盘中执行、右侧 Agent 讨论、盘后报告的完整故事 |
| P1 | 安装故障排查 | Windows PowerShell、Claude/Codex CLI、端口占用、Node/Python 版本的常见错误 |
| P2 | 对外发布材料 | 准备 V2EX/掘金/知乎/B站的项目介绍，强调本地可控和模拟盘优先 |

## Phase 6: 实盘准入 ⛔ 未完成

目标：真实资金交易前，把订单边界、人工确认和熔断能力补齐。Phase 6 未完成前，`--mode live` 不允许视为真实交易能力。

必须完成：

| 优先级 | 项 | 说明 |
|--------|----|------|
| P0 | BrokerAdapter | 在 `alphaclaude.engine` 内定义券商适配接口，隔离模拟成交和真实下单 |
| P0 | PAPER_ONLY 安全闸门 | 默认禁止实盘；需要 `.env` 和运行时指令双重确认 |
| P0 | 订单幂等 | 每笔订单有唯一 `trade_id`，重启/重试不能重复下单 |
| P0 | 人工确认流 | 大额或所有实盘订单必须走二次确认 |
| P1 | 多持仓相关性风控 | 高相关持仓自动下调仓位上限 |
| P1 | 大额交易风控辩论 | 借鉴 TradingAgents 的 Aggressive/Conservative/Neutral 三方风控审查 |

准入门槛：

- 模拟盘连续运行不少于 1 个月。
- 至少 30 笔有效交易。
- 胜率、回撤、夏普和异常恢复能力达到人工确认标准。
- 实盘前必须完成一次小资金 dry-run 和断线/重启演练。

## 暂不做

| 项 | 原因 |
|----|------|
| MCP Server 包装 | 当前 Claude Code/Codex 直接调用包内 CLI 已足够；不是赚钱或安全瓶颈 |
| 恢复旧单文件 Dashboard | React 组件化已解决 K 线生命周期和 Agent 终端扩展问题 |
| 跨市场泛化 | 项目优势是 A 股 T+1、政策市和本地工具链，不应过早稀释 |
| LangGraph 全量迁移 | 当前引擎循环简单可测；等 Agent 协作复杂度明显上升再评估 |

## 验证基线

文档或入口变更后至少运行：

```powershell
npm run dashboard:test
npm run dashboard:build
python -m compileall -q src\alphaclaude
```

引擎核心变更后增加：

```powershell
python -m pytest -q
alphaclaude --help
alphaclaude engine start --help
alphaclaude tools quote --help
```
