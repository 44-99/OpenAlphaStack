# AlphaClaude

基于 **Claude Code** 驱动的 AI 股票交易机器人。每日 A 股市场分析和个股推荐，通过飞书（Lark）推送。

## 功能特性

- **定时报告** — 9:00 早盘简报、12:00 午间更新、15:30 收盘总结（交易日）
- **多因子选股** — 基于 akshare 的短线（1-5天）和中线（1-4周）选股
- **交互对话** — 飞书私聊/群聊中询问个股、大盘、持仓
- **自定义任务** — `/task 每天早上8点分析茅台` — 自然语言创建定时任务
- **跨群查询** — `/group <群ID> <问题>` — 私聊中查询任意已注册群聊
- **双层记忆** — Claude Code transcript + 项目 memory 文件，每 12 小时自动整合
- **技能系统** — `skills/` 下的 `.md` 文件配 YAML frontmatter 触发器，启动时热加载
- **订阅推送** — `/sub` `/unsub` `/status` — 按群自主订阅每日推送

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                      飞书 (Lark)                         │
│               WebSocket 长连接                           │
└──────────────────────┬──────────────────────────────────┘
                       │ 事件
                       ▼
┌─────────────────────────────────────────────────────────┐
│                      main.py                             │
│  消息编排 · 会话管理 · 指令处理 · 技能加载               │
└──┬────────┬────────┬────────┬────────┬─────────────────┘
   │        │        │        │        │
   ▼        ▼        ▼        ▼        ▼
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────┐
│memory│ │claude│ │stock │ │sched │ │ feishu/  │
│ .py  │ │ .py  │ │ .py  │ │ .py  │ │ auth bot │
│      │ │      │ │      │ │      │ │ group ws │
└──┬───┘ └──────┘ └──────┘ └──┬───┘ └──────────┘
   │                          │
   ▼                          ▼
┌──────┐              ┌──────────────┐
│config│              │  skills/     │
│ .py  │              │  SKILL.md    │
└──────┘              │  references/ │
                      │  scripts/    │
                      └──────────────┘
```

**依赖关系**（无循环）:

```
main ──→ memory, claude, stock, scheduler, feishu
scheduler ──→ memory, claude, stock, feishu
memory ──→ claude, config
```

## 设计理念

**Alpha** = 知识大脑 — 多维情报喂给 Agent：

| 来源 | 角色 |
|------|------|
| 实时行情（akshare） | 价格、成交量、换手率、板块资金 |
| 通达信公式（skills） | 实战检验的策略，编码为渐进式展开技能 |
| 新闻 / 研报（RAG） | 外部信息按需注入上下文 |
| 模型内置知识 | 预训练的金融概念、估值理论、市场机制 |

**Claude** = 执行核心 — Claude Code CLI 作为本地 Agent：

- **比替代方案更轻**: 无服务器基础设施、无进程池。`pip install` + 一个 CLI 二进制文件就是全部运行时。
- **强编码 + 高性价比**: Claude Code + DeepSeek 用于编排和分析。
- **继承一切**: 多轮对话、工具编排、会话管理、MCP 协议 — 全部内置在 Claude Code 中。我们不重复造轮子。
- **通用能力**: 超越股票交易 — 编程帮助、写作、知识问答、跨平台聊天 — Claude Code 有的能力 AlphaClaude 都有。

本项目专注于为 Claude Code Agent 装上"股票大脑"：Skills 作为策略知识，Python 脚本作为数据计算，飞书作为通信渠道。

### 为什么用无状态脚本而不是 MCP Server

Claude Code 内置的 Bash 工具足以满足所有工具需求：

| 维度 | 我们的方案 |
|------|-----------|
| **工具** | 项目根目录下的 Python 脚本 + `skills/*/scripts/` |
| **调用** | Bash 子进程（Claude Code 内置） |
| **状态** | 无状态 — 每次调用独立，即时返回 |
| **复杂度** | 几乎零运维开销 — 无进程管理、无生命周期、无回调基础设施 |

我们所有的工具都是轻量 Python 函数（akshare HTTP 调用、公式计算、向量搜索）。它们无状态、即时返回、无特殊要求。Claude Code 的 Bash 工具原生处理它们 — 不需要单独的 MCP server、进程池或回调系统。

当 Phase 3 自动交易需要专用信号监控进程时，它将是一个独立的 sidecar 守护进程，而非分层的服务网格。

## 快速开始

### 环境要求

- Python 3.10+
- [Claude Code](https://claude.ai/code) CLI 已安装
- 飞书开发者账号

### 安装

```bash
git clone https://github.com/44-99/AlphaClaude.git
cd AlphaClaude
pip install -r requirements.txt
```

### 配置

1. 在 [飞书开放平台](https://open.feishu.cn) 创建应用
2. 启用 **机器人** 能力并添加到应用中
3. 事件订阅选择 **WebSocket 长连接模式**
4. 订阅 `im.message.receive_v1` 事件
5. 授予权限: `im:message`、`im:message:read`、`im:message.group:read`
6. 复制 `.env.example` 为 `.env` 并填入凭证：

```bash
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_BOT_NAME=StockBot
FEISHU_BOT_OPEN_ID=ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Claude CLI
CLAUDE_CMD=C:\Users\YourName\AppData\Roaming\npm\claude.cmd
CLAUDE_TIMEOUT=120
```

### 运行

```bash
python main.py
```

启动在 8800 端口。健康检查: `http://localhost:8800/health`

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 + WebSocket 状态 |
| GET | `/subscribers` | 列出已订阅的聊天 ID |
| GET | `/sessions` | 列出活跃会话 |
| POST | `/trigger/now?session=morning` | 手动触发任务 (`morning`/`midday`/`closing`/`dream`) |

### Windows 开机自启

将 `start_bot.bat` 复制到 Windows 启动文件夹，即可开机自动启动。

## 机器人指令

| 指令 | 说明 |
|------|------|
| `/help` | 显示欢迎消息和指令列表 |
| `/sub` / `订阅` | 订阅每日推送 |
| `/unsub` / `退订` | 取消订阅 |
| `/status` / `推送状态` | 查看订阅状态 |
| `/task <描述>` | 创建自定义定时任务（如 `/task 每天早上8点分析茅台`） |
| `/task delete <id>` | 删除任务 |
| `/tasks` | 列出所有任务 |
| `/group <群ID> <问题>` | 跨群查询（仅私聊可用） |
| `/groups` | 列出已注册群聊 |
| `/new` / `新对话` | 重置对话上下文 |

## 交易策略

| 类型 | 筛选条件 |
|------|----------|
| **短线 (1-5天)** | 涨幅 2-9%, 换手率 3-20%, 量比 >1.5, 成交额 >1亿 |
| **中线 (1-4周)** | PE 0-50, PB 0-8, 涨幅 1-7%, 换手率 2-15%, 板块动量 |

每笔推荐均包含买入价、止损价和止盈价。

## 记忆系统

双层架构：

| 层级 | 位置 | 管理者 | 内容 |
|------|------|--------|------|
| Claude Code transcript | `~/.claude/projects/.../` | Claude Code | 完整对话历史 |
| 项目 memory 文件 | `data/memory/user/` 和 `data/memory/group/` | 整合任务 | 用户画像、偏好、话题摘要 |

每日 3:17 和 15:17 执行整合，扫描过去 12 小时内修改的 transcript 并更新 memory 文件。新会话在首条消息时注入对应 memory。

## 项目结构

```
AlphaClaude/
├── main.py          — 消息编排、会话管理、指令处理、技能加载、FastAPI
├── memory.py        — 用户/群聊记忆系统、transcript 整合
├── claude.py        — Claude Code CLI 封装
├── stock.py         — 行情数据 (akshare)、多因子筛选
├── scheduler.py     — APScheduler 定时任务 + 动态任务 CRUD
├── config.py        — 环境变量加载
├── feishu/          — 飞书 SDK 集成
│   ├── auth.py      — 租户访问令牌
│   ├── bot.py       — send_text / send_post / reply_message / parse_event
│   ├── group.py     — 群聊成员检查
│   ├── user.py      — 用户标签查询
│   └── ws.py        — lark-oapi WebSocket 监听
├── tools/           — Claude Code CLI 工具 (JSON 进/出, 无状态)
│   ├── quote.py     — 实时行情 & 大盘概况
│   ├── technical.py — 技术指标 (MA/MACD/RSI/KDJ/布林带)
│   ├── fundamental.py — PE/PB/ROE/营收增速
│   ├── flow.py      — 资金流向、北向资金、主力动向
│   ├── news.py      — 公告、研报、情绪分析
│   ├── screen.py    — 可插拔多因子筛选
│   └── backtest.py  — 历史形态回测
├── skills/          — 策略框架 + 工具编排
│   └── example-stock-alert.md
├── strategies/      — 筛选/回测策略配置文件
├── data/            — 运行时数据 (会话、订阅、任务、记忆、缓存)
├── CLAUDE.md        — Claude Code 项目指令
└── requirements.txt
```

## 技能系统

技能采用**渐进式展开**设计，保持初始 prompt 精简，同时让 Claude Code 按需获取深度领域知识：

```
skills/ma-cross/
├── SKILL.md              # 路由: 触发词、何时使用、加载哪个 reference
├── references/
│   ├── golden-cross.md   # 金叉买入信号: 公式逻辑、参数、止损
│   └── death-cross.md    # 死叉卖出信号: 同上结构
└── scripts/
    └── ma_signal.py      # Claude Code 按需执行: akshare → 计算交叉信号
```

- **SKILL.md** 启动时加载（Claude Code 注入上下文）。充当路由器 — _何时_ 使用该技能以及 _读取哪个_ reference 文件。
- **references/** 按需加载。包含公式理论、参数依据、市场条件说明、调优指南。
- **scripts/** 由 Claude Code 通过 Bash 执行。获取数据并计算信号的 Python 脚本。

## 未来工作

四阶段路线图，按依赖关系和风险递进排列——每一阶段都是下一阶段的前提。

### Phase 1: Tool Layer — 给 Claude Code 一个交易工作站 (P0)

将 `stock.py` 的单体"一把抓"拆解为离散的 CLI 工具。每个工具单一职责：JSON 进、JSON 出。Claude Code 通过 Bash 按需调用，自主决定查询什么数据、如何组合分析结果。

| ID | Tool | Command | Description |
|----|------|---------|-------------|
| 1.1 | `quote` | `python tools/quote.py 600519` | 实时价格/涨跌幅/换手率/量比。`market` 查看大盘概况。 |
| 1.2 | `technical` | `python tools/technical.py 600519 --all` | MA (5/10/20/60)、MACD、RSI、KDJ、布林带、量价分析 (pandas-ta) |
| 1.3 | `fundamental` | `python tools/fundamental.py 600519` | PE / PB / ROE / 营收增速 / 行业分位排名 |
| 1.4 | `flow` | `python tools/flow.py 600519` | 北向资金、主力净流入、大单方向 |
| 1.5 | `news` | `python tools/news.py 600519` | 近期公告、研报评级、市场情绪聚合 |
| 1.6 | `screen` | `python tools/screen.py -s breakout` | 可插拔多因子筛选。策略即 JSON 配置文件 (`strategies/`)，不硬编码阈值。 |
| 1.7 | `backtest` | `python tools/backtest.py 600519 -s ma_cross` | 轻量历史回测。"该形态出现 N 次，胜率 X%，平均收益 Y%"。 |
| 1.8 | `watch_001` | `python tools/watch_001.py` | 自选股管理：增删查改，持仓盈亏概览 |

**为什么用 CLI 而不是 MCP Server**：每个调用无状态、即时返回，Claude Code 内置的 Bash 工具原生支持。零基础设施开销。

> Phase 1 已基本完成，7/8 工具已实现。这是所有后续阶段的底座——没有标准化工具接口，策略框架和模拟交易都无从谈起。

---

### Phase 2: 策略框架 + 模拟盘验证 — 可信度闭环 (P0)

**这一阶段是整个系统的信任基础。** 核心逻辑：策略产生信号 → 模拟盘执行 → 实盘数据跟踪 → 胜率/P&L/夏普公开可查 → 数据证明策略可靠 → 才有资格进入实盘。

| ID | Feature | Description |
|----|---------|-------------|
| 2.1 | **Skill 系统升级** | Skill 从「关键词→prompt 注入」升级为**带工具编排的策略框架**。YAML frontmatter 新增 `tools:` 字段声明调用链（如放量突破 = `quote → technical(量比) → flow(主力确认) → 输出决策`）。`references/` 存放策略理论、参数依据、调优指南。 |
| 2.2 | **交易跟踪** | `/track <代码> <买入价> <止损> <止盈>` 记录每笔推荐。每日收盘 cron 对比目标价 vs 实际价。`/track status` 输出累计胜率、P&L、夏普比率。**这是可信度的数据来源。** |
| 2.3 | **模拟盘引擎** | **Phase 3 实盘的前置关卡。** 虚拟账户 + 初始资金。每次 skill 策略产生交易信号后自动在模拟盘中以实时市价成交。每日核算持仓盈亏、累计收益率、胜率、最大回撤、夏普比率。达到预设标准（如 30 笔交易 + 胜率 >55% + 夏普 >1.0）后，该策略获得实盘准入资格。**区别于回测**：回测是历史拟合，模拟盘是实时市场下的前向检验——暴露的是完整的策略+执行链路在真实环境中的表现，而非仅验证公式本身。 |
| 2.4 | **富文本战报** | 每日 15:30 收盘总结用飞书 `send_post` 卡片格式：涨的绿色、跌的红色、胜率汇总、模拟盘净值曲线。卡片适合截图分享→其他群→自然增长。 |
| 2.5 | **自选股系统** | `/watch 600519` `/unwatch` `/portfolio`。自选股触发异动条件（用户设定价格阈值）时主动推送飞书通知。 |
| 2.6 | **一键部署** | Docker Compose + 预配置 `.env` 模板。非程序员 5 分钟内从零到跑起来。稳定后考虑飞书应用商店上架。 |
| 2.7 | **可靠性加固** | 进程守护 (systemd / Docker restart)、结构化日志、崩溃飞书告警。Session 排队替代"正忙，请稍后"。 |

> **模拟盘通过标准是进入 Phase 3 的硬性条件**——没有经过前向检验的策略不能接触真实资金。

---

### Phase 3: 实盘交易管线 (P1) — 准入条件：Phase 2 模拟盘验证通过

| ID | Feature | Description |
|----|---------|-------------|
| 3.1 | **券商接入** | `tools/trade.py` 封装券商 API。首选东方财富 OpenAPI（低门槛、RESTful、散户友好）。后期可选 QMT/PTrade 等专业通道。JSON 订单格式：`{symbol, action, quantity, price, order_type}`。 |
| 3.2 | **交易确认流** | 每笔订单触发飞书交互卡片："*即将买入 贵州茅台 100 股 @ ¥1850，确认？*" 用户点击"确认"→ 订单执行 → 成交回报推送。**绝不静默自动交易。** |
| 3.3 | **策略→信号→交易 全链路** | 端到端：skill 框架 (Phase 2.1) 触发工具链 → Claude Code 产生结构化交易信号 → 飞书确认卡片 → `trade.py` 执行 → P&L 跟踪 (Phase 2.2)。人始终在环路中。 |
| 3.4 | **条件单** | 止损/止盈订单持久化到 `data/orders.json`。独立监控进程每 30s 检查价格，触发条件即执行。重启后自动恢复。 |

---

### Phase 4: 实时智能增强 (P2) — 锦上添花

前三阶段已构成完整的「分析→推荐→跟踪→交易」闭环。Phase 4 是在此之上的体验和深度增强，投入产出比相对较低，不阻塞核心业务。

| ID | Feature | Description |
|----|---------|-------------|
| 4.1 | **盘中异动引擎** | 独立进程每 30-60s 轮询 akshare。检测：价格突破、成交量异动、涨跌停逼近、指数拐点。飞书卡片推送订阅用户。 |
| 4.2 | **深度回测报告** | `tools/backtest.py` 升级：参数网格搜索优化、蒙特卡洛模拟、行业对标基准。输出胜率、夏普、最大回撤、盈亏比。 |
| 4.3 | **新闻情绪管线** | 定时抓取财经新闻 → 轻量情感分类 → 按股票关键词索引的 JSON 缓存 → 用户查询时注入上下文。不上重向量数据库。 |

## 许可证

MIT © AlphaClaude
