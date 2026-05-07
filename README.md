# AlphaClaude

**数据层**：腾讯→新浪→akshare 三级 fallback，20 个 CLI 工具覆盖行情/技术/基本面/资金/形态/信号/风控/引擎/报表。
**策略层**：3 条场景化技能管线 + 7 条交易铁律前置约束，description-based 智能激活。
**引擎层**：统一 Agent 引擎（回测/模拟盘/实盘三模式共享同一代码路径），盘后批量分析 + 次日机械执行。
**运维层**：Docker 部署、结构化日志、双层记忆系统、双模式 CI（快速 CI + Agent 回测 CI）。

## 功能特性

- **盘后批量 + 盘中执行** — 次日盘前 8:00 运行管线：Phase 0 子智能体（Claude Code 查数据做研究）→ Phase 1-3 主阶段（API + Tool Use 输出结构化决策 JSON）→ 产出 plan.json。盘中 Python 机械执行，零 LLM
- **核心+卫星仓位 (50/30/20)** — 核心仓政策驱动持有 1-4 周，卫星仓技术信号快进快出 1-5 天，20% 现金应对极端机会
- **策略闭环** — Agent 回测（含 Claude Code 判断层）→ 模拟盘验证 → 策略迭代 → 实盘准入，三模式共享完全相同的代码路径
- **多因子选股** — 腾讯行情主源（88字段）、新浪/akshare fallback，短线/中线/热钱 3 策略
- **交互对话** — 飞书私聊/群聊中询问个股、大盘、持仓，自然语言开启/关闭实盘
- **双层记忆** — Claude Code transcript + 项目 memory 文件，每 12 小时自动整合
- **风控硬约束** — 信号校验层 + 确定性风险计算器 + 双层安全闸门。紧急时（大盘跌>3%）自动暂停并唤醒 Claude Code

## 架构

```
┌─ 盘后→次日盘前 (15:00 → 8:00) ───────────────────────────┐
│  Phase 0: 3 个 Claude Code 子智能体并行（完整工具访问）     │
│  A=宏观政策 B=板块轮动 C=交易复盘 → 各输出 500 字摘要      │
│                                                            │
│  Phase 1-3: API + Tool Use（API 层面强制结构化 JSON）      │
│  定方向 → 选标的 → 调仓 + risk.py 风控 → plan.json         │
└──────────────────────────┬─────────────────────────────────┘
                           │ plan.json
                           ▼
┌─ 盘中 (9:15 → 15:00) ────────────────────────────────────┐
│  Python 引擎机械执行（零 LLM 调用）                        │
│  9:25  持仓调整 · 每 5s 止盈止损 · 候选买入(限价)         │
│  规则信号(卫星) · 紧急: 大盘>3%跌 → API+Tool Use 响应     │
└──────────────────────────────────────────────────────────┘

模式: backtest / paper / live  (完全相同代码路径)
```

**依赖关系**（无循环）:

```
main ──→ memory, claude, scheduler, feishu, config
scheduler ──→ memory, claude, feishu
memory ──→ claude, config
```

## 设计理念

以 Claude Code CLI 为策略大脑，Python 引擎为执行躯干。`tools/` 下的 20 个 CLI 脚本提供 A 股数据（腾讯 → 新浪 → akshare 三级 fallback），`skills/` 提供交易策略框架。飞书作为通信渠道。管线采用混合架构：**子智能体走 Claude Code（完整工具/记忆/技能上下文）**，**结构化决策走 API + Tool Use（JSON Schema 强制，零解析）**。回测、模拟盘、实盘三模式共享完全相同的代码路径——在回测里证明有效的策略，在实盘里同样有效。详见 [docs/architecture.md](docs/architecture.md) 和 [docs/roadmap.md](docs/roadmap.md)。

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

# LLM API（管线结构化输出用，Tool Use 保证 JSON）
ANTHROPIC_AUTH_TOKEN=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-v4-pro
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
├── main.py              — 消息编排、会话管理、FastAPI 入口
├── claude.py            — Claude Code CLI 封装
├── memory.py            — 双层记忆系统（transcript + 项目 memory）
├── scheduler.py         — 定时任务调度 + 动态 CRUD
├── config.py / stock.py — 环境配置与内部数据层
├── feishu/              — 飞书 SDK（WebSocket 长连接 / 消息 / 鉴权）
├── tools/               — 20 个 CLI 工具（行情/技术/基本面/资金/形态/风控/引擎）
├── skills/              — 3 条场景化策略技能（渐进式展开 + references 深度知识）
├── .github/workflows/   — CI/CD（快速 CI + Agent 回测 + Docker 部署）
├── data/output/         — 引擎运行时输出（ledger / state / plan，三模式统一）
├── CLAUDE.md            — Claude Code 系统提示词
└── requirements.txt
```

## 技能系统

场景化设计：`SKILL.md` 路由 + `references/` 深度知识。description-based 激活（非关键词触发），Agent 根据用户意图自主选择技能管线。`trading-principles.md` 作为前置技能始终加载。详见 [docs/skills.md](docs/skills.md)。

## 路线图

四阶段路线图详见 [docs/roadmap.md](docs/roadmap.md)。当前进度：Phase 1 完成，Phase 2 全部完成（2.1-2.10 已交付），进入 Phase 3 实盘准入准备。

## 许可证

MIT © AlphaClaude
