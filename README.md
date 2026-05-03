# AlphaClaude

![AlphaClaude](docs/poster.png)

基于 **Claude Code** 驱动的 A 股 Agent 量化交易系统，运行在飞书（Lark）上。

**数据层**：腾讯→新浪→akshare 三级 fallback，15 个 CLI 工具覆盖行情/技术/基本面/资金/形态/信号/风控。
**策略层**：3 条场景化技能管线 + 7 条交易铁律前置约束，description-based 智能激活。
**引擎层**：统一 Agent 引擎（回测/模拟盘/实盘三模式共享同一代码路径），双速架构（Python 快车道 + Claude Code 慢车道）。
**运维层**：Docker 部署、结构化日志、双层记忆系统、双模式 CI（快速 CI + Agent 回测 CI）。

## 功能特性

- **Agent 自主交易** — 统一引擎驱动，回测/模拟盘/实盘三种模式共享代码路径。Claude Code 每天一个会话，从盘前竞价到收盘复盘全程自主决策
- **策略闭环** — Agent 回测（含 Claude Code 判断层）→ 模拟盘验证 → 策略迭代 → 实盘准入
- **多因子选股** — 腾讯行情主源（88字段）、新浪/akshare fallback，短线/中线/热钱 3 策略
- **交互对话** — 飞书私聊/群聊中询问个股、大盘、持仓，自然语言开启/关闭实盘
- **双层记忆** — Claude Code transcript + 项目 memory 文件，每 12 小时自动整合
- **技能系统** — 3 个场景化技能管线，description-based 激活，渐进式展开
- **风控硬约束** — 信号校验层 + 确定性风险计算器 + 双层安全闸门

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                       飞书 (Lark)                           │
│                WebSocket 长连接                             │
└───────────────────────┬─────────────────────────────────────┘
                        │ 事件
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                       main.py                               │
│   消息编排 · 会话管理 · 指令处理 · 技能加载                  │
└──┬─────────┬──────────┬──────────┬──────────┬───────────────┘
   │         │          │          │          │
   ▼         ▼          ▼          ▼          ▼
┌──────┐ ┌──────┐ ┌────────┐ ┌──────┐ ┌──────────────┐
│memory│ │claude│ │scheduler│ │config│ │  feishu/     │
│ .py  │ │ .py  │ │ .py    │ │ .py  │ │ auth bot ws  │
└──┬───┘ └──┬───┘ └───┬────┘ └──────┘ └──────────────┘
   │        │         │
   ▼        ▼         ▼
┌─────────────────────────────────────────────────────────────┐
│                   Unified Agent Engine                       │
│                                                             │
│  ┌─ Python 快车道 ──────────────────────────────────────┐  │
│  │  tools/: 15 CLI 工具 (JSON 进/出，无状态)            │  │
│  │  · 行情/技术/基本面/资金/形态/情绪/信号/风控         │  │
│  │  · 止盈止损监控 · 规则信号触发 · 异动检测            │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ Claude Code 慢车道 ─────────────────────────────────┐  │
│  │  skills/: 3 条技能管线 + 7 条交易铁律                │  │
│  │  · 每天一个会话 (盘前→盘中→盘后)                     │  │
│  │  · 决策账本跨会话连续 · 策略判断 · 计划产出          │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  模式: backtest / paper / live  (完全相同代码路径)         │
└─────────────────────────────────────────────────────────────┘
```

**依赖关系**（无循环）:

```
main ──→ memory, claude, scheduler, feishu, config
scheduler ──→ memory, claude, feishu
memory ──→ claude, config
```

## 设计理念

以 Claude Code CLI 为策略大脑，Python 引擎为执行躯干。`tools/` 下的 15 个 CLI 脚本提供 A 股数据（腾讯 → 新浪 → akshare 三级 fallback），`skills/` 提供交易策略框架。飞书作为通信渠道。回测、模拟盘、实盘三模式共享完全相同的代码路径——在回测里证明有效的策略，在实盘里同样有效。详见 [docs/architecture.md](docs/architecture.md) 和 [docs/roadmap.md](docs/roadmap.md)。

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
CLAUDE_TIMEOUT=300
```

### 运行

```bash
python main.py
```

启动在 8800 端口。健康检查: `http://localhost:8800/health`

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 + WebSocket 状态 + 引擎状态 |
| GET | `/subscribers` | 列出已订阅的聊天 ID |
| GET | `/sessions` | 列出活跃会话 |

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

策略以场景化技能形式组织，渐进式展开（SKILL.md 路由 + references/ 深度知识）：

| 技能 | 场景 | 核心分析框架 |
|------|------|-------------|
| `stock-analyzer` | 个股深度分析 | 趋势→信号→位置→风险→输出（6 阶段管线） |
| `market-analyzer` | 市场研判 | 情绪周期、龙头识别、板块轮动 |
| `stock-screener` | 多因子选股 | 短线/中线/热钱 3 种策略，全市场扫描 |

11 套经典策略（金叉、放量突破、缩量回踩、底部放量、一阳三阴、箱体震荡、缠论、波浪、龙头、情绪周期等）作为 references 深度知识按需加载。每笔推荐均包含买入价、止损价和止盈价。

## 记忆系统

双层架构：Claude Code transcript（完整对话）+ 项目 memory 文件（用户画像/偏好摘要）。每日两次自动整合，新会话注入对应 memory。详见 [docs/architecture.md#记忆系统](docs/architecture.md#记忆系统)。

## 项目结构

```
AlphaClaude/
├── main.py          — 消息编排、会话管理、指令处理、技能加载、FastAPI
├── memory.py        — 用户/群聊记忆系统、transcript 整合
├── claude.py        — Claude Code CLI 封装
├── scheduler.py     — APScheduler 定时任务 + 动态任务 CRUD
├── config.py        — 环境变量加载
├── stock.py         — 机器人内部数据层（定时报告 & 上下文注入），akshare 批量扫描
├── feishu/          — 飞书 SDK 集成
│   ├── auth.py      — 租户访问令牌
│   ├── bot.py       — send_text / send_post / reply_message / parse_event
│   ├── group.py     — 群聊成员检查
│   ├── user.py      — 用户标签查询
│   └── ws.py        — lark-oapi WebSocket 监听
├── tools/           — CLI 工具，Claude Code 通过 Bash 调用 (JSON 进/出，无状态)
│   ├── quote.py     — 实时行情 & 大盘概况
│   ├── technical.py — 技术指标 (MA/MACD/RSI/KDJ/布林带)
│   ├── fundamental.py — PE/PB/ROE/营收增速/行业对比
│   ├── flow.py      — 资金流向、北向资金、主力动向
│   ├── news.py      — 公告、研报、情绪分析
│   ├── screen.py    — 多因子筛选（策略内联为 Python 常量）
│   ├── backtest.py  — 历史形态回测（单股单策略）
│   ├── trend.py     — MA排列/交叉/乖离/趋势状态
│   ├── signal_detector.py — 5 种入场信号检测
│   ├── pivot.py     — 枢轴点/箱体/缠论中枢
│   ├── fibonacci.py — 斐波那契回撤/扩展位
│   ├── sentiment.py — 换手热度/量能/ATR/均线粘合/情绪评分
│   ├── portfolio.py — 自选股增删查改、持仓盈亏概览
│   ├── risk.py      — 确定性风险计算（波动率/仓位/回撤/相关性）
│   └── signal.py    — 交易信号硬校验 + 审计日志写入
├── skills/          — 场景化策略技能（渐进式展开）
│   ├── trading-principles.md    — 前置技能：7 条交易铁律，始终加载
│   ├── stock-analyzer/SKILL.md  — 个股分析管线 + references/
│   ├── market-analyzer/SKILL.md — 市场分析管线 + references/
│   └── stock-screener/SKILL.md  — 选股管线 + references/
├── data/            — 运行时数据
│   └── output/      — Agent 引擎输出 (ledger/state/plan，三种模式统一)
├── CLAUDE.md        — Claude Code 系统提示词（工具目录 + 交易纪律）
└── requirements.txt
```

## 技能系统

场景化设计：`SKILL.md` 路由 + `references/` 深度知识。description-based 激活（非关键词触发），Agent 根据用户意图自主选择技能管线。`trading-principles.md` 作为前置技能始终加载。详见 [docs/skills.md](docs/skills.md)。

## 路线图

四阶段路线图详见 [docs/roadmap.md](docs/roadmap.md)。当前进度：Phase 1 完成，Phase 2 进行中（2.1-2.3 已完成，2.4 统一 Agent 引擎开发中）。

## 许可证

MIT © AlphaClaude
