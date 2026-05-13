# 架构设计

## 设计理念

**Alpha** = 知识大脑 — 多维情报喂给 Agent：

| 来源 | 角色 |
|------|------|
| 实时行情（腾讯 qt.gtimg.cn 主源 + 新浪 hq.sinajs.cn + akshare Sina 后端 fallback） | 价格、成交量、换手率、PE、PB、量比 |
| 通达信公式（skills） | 实战检验的策略，编码为渐进式展开技能 |
| 新闻 / 研报（RAG） | 外部信息按需注入上下文 |
| 模型内置知识 | 预训练的金融概念、估值理论、市场机制 |

**Claude** = 执行核心 — Claude Code CLI 作为本地 Agent：

- **比替代方案更轻**: 无服务器基础设施、无进程池。`pip install` + 一个 CLI 二进制文件就是全部运行时。
- **强编码 + 高性价比**: Claude Code + DeepSeek 用于编排和分析。
- **继承一切**: 多轮对话、工具编排、会话管理、MCP 协议 — 全部内置在 Claude Code 中。我们不重复造轮子。
- **通用能力**: 超越股票交易 — 编程帮助、写作、知识问答、跨平台聊天 — Claude Code 有的能力 AlphaClaude 都有。

本项目专注于为 Claude Code Agent 装上"股票大脑"：Skills 作为策略知识，`src/alphaclaude/tools/` 下的 CLI 模块（腾讯→新浪→akshare）作为 Claude Code 的数据计算层，`stock.py` 为机器人内部定时报告和上下文注入提供批量数据，飞书作为通信渠道。

## 为什么用无状态脚本而不是 MCP Server

Claude Code 内置的 Bash 工具足以满足所有工具需求：

| 维度 | 我们的方案 |
|------|-----------|
| **工具** | `src/alphaclaude/tools/` 下的 Python CLI 模块 |
| **调用** | Bash 子进程（Claude Code 内置），开发态用 `python -m alphaclaude.tools.<tool>` |
| **状态** | 无状态 — 每次调用独立，即时返回 |
| **复杂度** | 几乎零运维开销 — 无进程管理、无生命周期、无回调基础设施 |

我们所有的工具都是轻量 Python 函数（腾讯/Sina HTTP 调用、公式计算）。它们无状态、即时返回、无特殊要求。Claude Code 的 Bash 工具原生处理它们 — 不需要单独的 MCP server、进程池或回调系统。

当 Phase 3 自动交易需要专用信号监控进程时，它将是一个独立的 sidecar 守护进程，而非分层的服务网格。

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                      飞书 (Lark)                         │
│               WebSocket 长连接                           │
└──────────────────────┬──────────────────────────────────┘
                       │ 事件
                       ▼
┌─────────────────────────────────────────────────────────┐
│              src/alphaclaude/app/main.py                 │
│  消息编排 · 会话管理 · 指令处理 · 技能加载 · FastAPI     │
└──┬────────┬────────┬────────┬────────┬────────┬──────────┘
   │        │        │        │        │        │
   ▼        ▼        ▼        ▼        ▼        ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────┐
│memory│ │claude│ │sched │ │config│ │stock │ │ feishu/  │
│ .py  │ │ .py  │ │ .py  │ │ .py  │ │ .py  │ │ auth bot │
│      │ │      │ │      │ │      │ │      │ │ group ws │
└──┬───┘ └──┬───┘ └──┬───┘ └──────┘ └──┬───┘ └──────────┘
   │        │        │                 │
   ▼        ▼        ▼                 ▼
┌──────┐ ┌──────┐ ┌──────────────┐
│ data/│ │src/  │ │  skills/     │
│mem/c │ │ CLI  │ │  SKILL.md    │
│ache  │ │ JSON │ │  references/ │
└──────┘ └──────┘ └──────────────┘
```

**依赖关系**（无循环）:

```
alphaclaude.app.main ──→ memory, claude, scheduler, feishu, config, stock
scheduler ──→ memory, claude, feishu, stock
memory ──→ claude, config
alphaclaude.engine ──→ alphaclaude.tools, config, data/output
```

## 包内模块边界

当前代码已从根目录脚本和旧 `tools/` 目录迁入 `src/alphaclaude/`，避免应用入口、交易引擎、工具脚本和测试互相依赖隐式路径。

| 包 | 职责 |
|----|------|
| `alphaclaude.app` | 飞书机器人、FastAPI、会话和指令编排 |
| `alphaclaude.engine` | 回测/模拟盘/预留 live 引擎、状态、计划、账本、执行、盘前计划生成、盘中快车道、盘后报告 |
| `alphaclaude.tools` | Claude Code 可调用的无状态 CLI 工具和报表/风控/信号工具 |
| `alphaclaude.paths` | 项目根目录、数据目录等路径解析，避免包内代码依赖当前工作目录 |

## 引擎流程

```
┌─ 盘前计划生成 ──────────────────────────────────────────┐
│  OvernightPipeline                                      │
│  · Claude Code 子任务：宏观政策 / 板块轮动 / 交易复盘   │
│  · API Tool Use：定方向 / 选标的 / 调仓                 │
│  · Python 风控：signal + risk 硬校验                    │
│  · 输出 plan.json                                       │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─ 盘中执行 ──────────────────────────────────────────────┐
│  FastLane                                               │
│  · 候选买入和持仓调整                                   │
│  · 止盈、止损、到期卖出                                 │
│  · 规则信号扫描和去重                                   │
│  · 紧急事件进入事件队列                                 │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─ 状态与审计 ────────────────────────────────────────────┐
│  EngineState / PlanV2 / Ledger / EventQueue             │
│  · state.json：现金、持仓、风控状态                      │
│  · plan.json：方向、候选、调仓、策略变体                 │
│  · ledger.jsonl：成交、拒单、风控和审计记录              │
└─────────────────────────────────────────────────────────┘
```

盘后不生成新的 `plan.json`。收盘后只运行 Python 汇总报告，记录净值、成交、持仓、盈亏、异常和风控事件，并按配置推送飞书。这样能保证交易决策来自盘前，盘中只机械执行既定计划。

当前 `backtest` 和 `paper` 共享包内核心。`live` 模式入口保留，但必须等 BrokerAdapter、人工确认、订单幂等和安全闸门完成后才能准入。

开发态命令示例：

```bash
PYTHONPATH=src python -m alphaclaude.app.cli
PYTHONPATH=src python -m alphaclaude.engine.cli --mode backtest --start 2024-01-01 --end 2024-06-30 -u default
PYTHONPATH=src python -m alphaclaude.tools.quote 600519
```

安装态命令示例：

```bash
alphaclaude
alphaclaude-engine --mode paper -u default
python -m alphaclaude.tools.quote 600519
```

## 数据源

实地测试对比后确定的数据源优先级：

| 优先级 | 数据源 | 速度 | 字段 | 用途 |
|--------|--------|------|------|------|
| 1 (主) | 腾讯 `qt.gtimg.cn` | ~0.07s | 88（含PE/PB/换手率/量比/市值） | 实时行情、全市场选股 |
| 2 | 新浪 `hq.sinajs.cn` | ~0.1s | 34（基础OHLCV） | 行情 fallback、K线历史 |
| 3 | akshare Sina 后端 | ~25s | 14（价量） | 代码列表缓存、最后手段 |

- **腾讯 API**：不依赖 eastmoney push2，不受 IP 封锁影响，速度最快、字段最全
- **K线历史**：新浪 K线 API 提供日线 OHLCV，最大 ~2000 天
- **财务/资金/新闻**：akshare 东方财富接口（非 push2 端点，可用）
- **efinance / pytdx**：实测不可用

每个工具内部封装 fallback 逻辑，优先腾讯 → 失败自动切换新浪 → akshare 最后手段。

## 记忆系统

双层架构：

| 层级 | 位置 | 管理者 | 内容 |
|------|------|--------|------|
| Claude Code transcript | `~/.claude/projects/.../` | Claude Code | 完整对话历史 |
| 项目 memory 文件 | `data/memory/user/` 和 `data/memory/group/` | 整合任务 | 用户画像、偏好、话题摘要 |

每日 3:17 和 15:17 执行整合，扫描过去 12 小时内修改的 transcript 并更新 memory 文件。新会话在首条消息时注入对应 memory。
