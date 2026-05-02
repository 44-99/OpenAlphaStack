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

以 Claude Code CLI 为执行核心，通过 Python 无状态脚本提供 A 股数据（腾讯 → 新浪 → akshare 三级 fallback），飞书作为通信渠道。详见 [docs/architecture.md](docs/architecture.md)。

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

双层架构：Claude Code transcript（完整对话）+ 项目 memory 文件（用户画像/偏好摘要）。每日两次自动整合，新会话注入对应 memory。详见 [docs/architecture.md#记忆系统](docs/architecture.md#记忆系统)。

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
├── tools/           — CLI 工具，Claude Code 通过 Bash 调用 (JSON 进/出，无状态)
│   ├── quote.py     — 实时行情 & 大盘概况
│   ├── technical.py — 技术指标 (MA/MACD/RSI/KDJ/布林带)
│   ├── fundamental.py — PE/PB/ROE/营收增速
│   ├── flow.py      — 资金流向、北向资金、主力动向
│   ├── news.py      — 公告、研报、情绪分析
│   ├── screen.py    — 可插拔多因子筛选
│   └── backtest.py  — 历史形态回测
├── skills/          — 策略技能 (Markdown + YAML frontmatter，渐进式展开)
│   ├── trading-principles.md  — 前置技能：交易铁律，始终加载
│   ├── ma-golden-cross/       — 均线金叉策略
│   ├── volume-breakout/       — 放量突破策略
│   ├── shrink-pullback/       — 缩量回踩策略
│   ├── dragon-head/           — 龙头策略
│   └── ... (共 11+ 策略技能)
├── strategies/      — 筛选/回测策略 JSON 配置文件
├── data/            — 运行时数据 (会话、订阅、任务、记忆、缓存)
├── CLAUDE.md        — Claude Code 系统提示词
└── requirements.txt
```

## 技能系统

渐进式展开设计：`SKILL.md` 路由 + `references/` 深度知识 + `scripts/` 计算脚本。YAML frontmatter 声明触发词和优先级。`trading-principles.md` 作为前置技能始终加载。详见 [docs/skills.md](docs/skills.md)。

## 未来工作

四阶段路线图详见 [docs/roadmap.md](docs/roadmap.md)。

## 许可证

MIT © AlphaClaude
