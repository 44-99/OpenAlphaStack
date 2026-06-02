# 架构设计

AlphaClaude 的核心定位是本地运行的 “交易软件 + AI IDE 工作台”。交易研究和策略判断交给 Claude Code / Codex 这类本机 Agent，行情、风控、回测、模拟盘和执行审计由 Python 包内模块承担，Dashboard 提供可视化盯盘和本机终端入口。

## 设计原则

- **本地优先**：核心服务运行在本机，数据落在 `data/`，Dashboard 默认面向本地/局域网使用。
- **Agent 不重复造轮子**：Claude Code 和 Codex CLI 已经有对话、工具调用和文件编辑能力，Dashboard 只负责把本机终端嵌入工作台。
- **交易执行可审计**：盘前 `plan.json`、盘中 `state.json`、成交 `ledger.jsonl` 和日志文件都是事实来源。
- **飞书降级为通知渠道**：深度分析和盘中调整以 Dashboard 为主，飞书保留状态查询、指令和关键告警。
- **实盘保守准入**：`live` 入口预留，但 BrokerAdapter、人工确认、订单幂等和安全闸门完成前不视为可用实盘能力。

## 三通道交互模型

```
┌─────────────────────────────────────────────────────────────┐
│ Web Dashboard                                                │
│ · React/Vite 工作台                                          │
│ · ECharts K线、指标、缓存管理                                │
│ · xterm 内嵌 PowerShell：Claude Code / Codex CLI             │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP / SSE / WebSocket
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ FastAPI App                                                  │
│ · Dashboard API、SSE、Agent terminal WebSocket               │
│ · 飞书 WebSocket 长连接                                      │
│ · Scheduler、会话、命令编排                                  │
└──────────────────────┬──────────────────────────────────────┘
                       │ 文件状态 + 包内模块
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ Engine / Tools / Data                                        │
│ · alphaclaude.engine: plan/state/ledger/execution/paper       │
│ · alphaclaude.tools: quote/technical/risk/signal/report       │
│ · data/output、data/cache、data/state                         │
└─────────────────────────────────────────────────────────────┘
```

## 包内模块边界

| 包/目录 | 职责 |
|---------|------|
| `src/alphaclaude/app/` | FastAPI 应用、Dashboard API/SSE/WebSocket、应用 CLI、会话和指令编排 |
| `src/alphaclaude/engine/` | 回测/模拟盘/预留 live 引擎、状态、计划、账本、执行、盘前计划生成、盘中快车道、盘后报告 |
| `src/alphaclaude/tools/` | Claude Code/Codex 可调用的无状态 CLI 工具和报表/风控/信号工具 |
| `src/alphaclaude/feishu/` | 飞书认证、机器人消息、群聊、用户和长连接适配 |
| `src/alphaclaude/paths.py` | 项目根目录、数据目录等路径解析 |
| `dashboard/` | React + Vite + TypeScript Dashboard 源码 |
| `scripts/` | Windows 开发启动、前后端热重载和端口清理脚本 |

依赖方向：

```
alphaclaude.app.main
  ├─ alphaclaude.app.dashboard
  ├─ alphaclaude.feishu
  ├─ scheduler / memory / claude / stock
  └─ alphaclaude.engine run registry

alphaclaude.engine
  ├─ alphaclaude.tools
  ├─ alphaclaude.paths
  └─ data/output/<run_id>

dashboard React app
  └─ FastAPI /api, /api/stream, /api/agent/terminal/*
```

## Dashboard 架构

当前 Dashboard 是 `dashboard/` 下的 React + Vite + TypeScript 应用。开发时由 Vite 在 `5173` 提供热重载，生产/日常使用时构建到 `dashboard/dist/`，由 FastAPI `/dashboard` 直接服务 `dist/index.html` 和静态资源。

### 前端组件

| 模块 | 说明 |
|------|------|
| `dashboard/src/App.tsx` | 页面骨架、左侧导航、顶部资产栏、右侧 Agent 面板、页面状态 |
| `dashboard/src/components/KlineChart.tsx` | ECharts 实例生命周期、数据加载、缩放/平移事件、触控板灵敏度控制 |
| `dashboard/src/charts/klineOption.ts` | 纯函数构造 K 线 option，便于测试和隔离 ECharts 配置 |
| `dashboard/src/components/AgentPanel.tsx` | xterm 终端，连接后端 WebSocket，并切换 Claude Code / Codex CLI |
| `dashboard/src/api.ts` | Dashboard API 客户端 |
| `dashboard/src/styles.css` | 暗色金融工作台主题、侧栏、K线 tooltip、终端样式 |

### 布局

```
┌──────────────────────────────────────────────────────────────┐
│ Topbar: 总资产 / 现金 / 持仓 / 当日盈亏 / K线缓存 / 引擎状态  │
├──────────────┬──────────────────────────────┬────────────────┤
│ Left Sidebar │ Workspace                    │ Agent Terminal │
│ · 可折叠     │ · K线图                      │ · 可折叠       │
│ · 可拖宽     │ · 周期/指标控制              │ · 可拖宽       │
│ · 真实图标   │ · 持仓/计划/成交/日志页面     │ · PowerShell   │
└──────────────┴──────────────────────────────┴────────────────┘
```

左侧侧边栏展开时显示图标和中文标签，折叠时只保留居中图标。右侧 Agent 面板折叠后显示窄 rail，展开后显示终端和 provider 切换按钮。

### K 线图

Dashboard 的 K 线交互由独立 React 组件管理，避免旧单文件实现里 ECharts 生命周期和状态更新互相污染。

已实现：

- 周期：`day`、`week`、`1m`、`5m`、`15m`、`60m`。
- 主图：candlestick + MA/EMA/BOLL 叠加。
- 副图：VOL。
- 交互：十字光标、item tooltip、拖拽平移、底部 slider、低灵敏度滚轮缩放。
- Tooltip：暗色玻璃金融卡片，只在鼠标命中 K 线 item 时显示，不在成交量图或空白区域保留旧 K 线卡片。

分钟级数据以 1 分钟数据为基础，5/15/60 分钟由后端 resample 生成并缓存。日线/周线也进入统一 K 线缓存链路，前端提供缓存大小显示和手动清理。

### Agent 终端

右侧 Agent 面板不是自定义聊天气泡，而是一个浏览器内嵌终端：

```
React AgentPanel
  └─ @xterm/xterm
      └─ WebSocket /api/agent/terminal/{provider}
          └─ FastAPI
              └─ winpty.PTY
                  └─ PowerShell
                      ├─ claude
                      └─ codex --no-alt-screen
```

后端会：

- 接收浏览器终端输入并写入 PTY。
- 读取 PTY 输出并通过 WebSocket 推回 xterm。
- 初始化 PowerShell UTF-8 编码。
- 对当前 PowerShell 进程设置临时 `ExecutionPolicy Bypass`，避免 npm CLI wrapper 被本机策略拦截。
- 根据 provider 自动执行本机 `claude` 或 `codex --no-alt-screen`。

这使用户可以在盯盘同时直接操作 Claude Code 或 Codex CLI，体验更接近 JetBrains/PyCharm 的工具窗口，而不是另造一个弱化版聊天 UI。

## FastAPI Dashboard API

| 端点 | 类型 | 说明 |
|------|------|------|
| `/dashboard` | HTTP | 返回构建后的 React Dashboard |
| `/api/state` | HTTP | 当前 run 的资金、持仓、净值和数据时间 |
| `/api/plan` | HTTP | 当前 run 的 `plan.json` |
| `/api/ledger` | HTTP | 当前 run 的成交账本 |
| `/api/kline/{code}` | HTTP | K 线 OHLCV，支持日/周/分钟缓存链路 |
| `/api/cache/status` | HTTP | K 线缓存文件数和大小 |
| `/api/cache/kline/clear` | HTTP POST | 清理所有 Dashboard K 线缓存层级 |
| `/api/watchlist` | HTTP | 用户自选股 |
| `/api/engine/status` | HTTP | 当前引擎运行状态 |
| `/api/stream` | SSE | Dashboard 实时事件和状态更新 |
| `/api/agent/terminal/{provider}` | WebSocket | Agent 终端 PTY 通道 |

历史兼容的 `/api/agent/{provider}/stream` 文本聊天接口仍可保留，但当前 UI 主路径是内嵌终端。

## 引擎流程

```
┌─ 盘前计划生成 ──────────────────────────────────────────┐
│ OvernightPipeline                                       │
│ · Claude Code 子任务：市场方向 / 候选标的 / 风控         │
│ · Python 工具：行情、技术、风控、信号校验                │
│ · 输出 plan.json                                        │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─ 盘中机械执行 ──────────────────────────────────────────┐
│ FastLane                                                │
│ · 读取 plan.json                                        │
│ · 候选买入、持仓调整、止盈止损、规则信号                 │
│ · 写 state.json / ledger.jsonl / event queue             │
│ · SSE 推送 Dashboard，关键事件推送飞书                   │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─ 盘后汇总 ──────────────────────────────────────────────┐
│ Python report                                           │
│ · 净值、成交、持仓、盈亏、异常和风控事件                 │
│ · 可选飞书推送                                          │
└─────────────────────────────────────────────────────────┘
```

非交易日或非盘前启动时，引擎可以进入观察模式，明确记录状态，不假装生成可执行交易计划。盘后不生成新的 `plan.json`，只做 Python 汇总报告。

## 运行控制面

`data/output/<run_id>/state.json` 是运行控制的事实来源。`alphaclaude.engine.run_registry` 扫描 `paper_*`、`backtest_*`、`live_*` 运行目录，读取 `engine_meta`，结合 PID liveness 判断运行态，并向 CLI、飞书命令和 Dashboard API 提供统一记录。

命令示例：

```bash
alphaclaude app start
alphaclaude engine start --mode backtest --start 2024-01-01 --end 2024-06-30 -u default
alphaclaude engine start --mode paper -u default --daemon
alphaclaude engine list
alphaclaude engine stop-running
alphaclaude tools quote 600519
```

## 数据源与缓存

行情数据优先级：

| 优先级 | 数据源 | 用途 |
|--------|--------|------|
| 1 | 腾讯 | 实时行情、K线、选股 |
| 2 | 新浪 | fallback |
| 3 | akshare | 代码列表、财务/资金/新闻、最后手段 |

Dashboard K 线缓存统一放在 `data/cache/kline/`，兼容旧分钟缓存目录 `data/cache/minute/`。手动清理缓存会清理 Dashboard K 线相关层级，不删除交易账本、计划、持仓状态或记忆文件。

## 开发启动

```bash
npm run dev
```

该命令会：

1. 运行 `scripts/dev-stop.ps1` 清理占用 `5173` 和 `8800` 的旧进程。
2. 启动 `uvicorn alphaclaude.app.main:app --reload`。
3. 等待 `http://127.0.0.1:8800/health` 就绪。
4. 启动 Vite 热重载服务。

生产/日常 Dashboard 使用：

```bash
npm run dashboard:build
alphaclaude app start
```

## 安全约束

- `.env` 和运行数据不提交。
- `data/output/`、`data/cache/`、`data/logs/` 和构建产物不提交。
- `live` 模式未准入前不得用于真实下单。
- 右侧 Agent 终端是本机 PowerShell，具备真实命令执行能力；默认只应在可信本机环境使用。
