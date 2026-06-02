# AlphaClaude

AlphaClaude 是一个面向 A 股的本地 Agent 交易工作台：Claude Code / Codex 负责研究、对话和策略判断，Python 包内引擎负责回测、模拟盘、状态、账本和盘中机械执行，React Dashboard 负责盯盘、K 线交互和本机 Agent 终端。

当前状态：

- Python 代码已迁入 `src/alphaclaude/`，应用入口、引擎、工具和飞书适配都按包结构组织。
- 回测和模拟盘共享 `src/alphaclaude/engine/` 包内核心。
- CLI 工具位于 `src/alphaclaude/tools/`，通过 `alphaclaude tools <tool>` 调用。
- Dashboard 已从旧单文件 HTML 迁移为 `dashboard/` 下的 React + Vite + TypeScript 应用。
- Dashboard K 线使用 ECharts 独立组件，支持日/周/分钟周期、MA/EMA/BOLL、成交量、十字光标、拖拽平移、滑块和低灵敏度滚轮缩放。
- Dashboard 右侧 `Agent` 面板是 `xterm` 内嵌本机 PowerShell 终端，可切换自动启动 Claude Code 或 Codex CLI。
- `live` 入口只是预留；券商适配器、订单确认、幂等和安全闸门完成前，不应视为实盘能力。

详细设计见 [docs/architecture.md](docs/architecture.md)，实施路线见 [docs/roadmap.md](docs/roadmap.md)。

## Quick Start

要求：

- Python 3.10+
- Node.js 20+ 和 npm
- Claude Code CLI
- 可选：Codex CLI
- 飞书开发者账号

安装：

```bash
git clone https://github.com/44-99/AlphaClaude.git
cd AlphaClaude
pip install -r requirements.txt
pip install -e .
npm install
```

配置：

复制 `.env.example` 为 `.env`，填入飞书、Claude CLI 和 Anthropic-compatible API 配置。核心变量包括：

```bash
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_BOT_NAME=StockBot
FEISHU_BOT_OPEN_ID=ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

CLAUDE_CMD=C:\Users\YourName\AppData\Roaming\npm\claude.cmd
CLAUDE_TIMEOUT=300

ANTHROPIC_AUTH_TOKEN=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_BASE_URL=https://api.example.com/anthropic
ANTHROPIC_MODEL=your-model
```

日常运行：

```bash
alphaclaude app start
```

健康检查：`http://localhost:8800/health`

## Dashboard

开发热重载：

```bash
npm run dev
```

- FastAPI/API/飞书服务：`http://localhost:8800`
- React/Vite Dashboard：`http://localhost:5173`
- Vite 会把 `/api/*` 和 WebSocket 代理到 `8800`。
- `npm run dev` 会先自动清理占用 `5173/8800` 的旧进程，再等待后端 `/health` 就绪，最后启动前端热重载。

生产/日常 Dashboard 构建：

```bash
npm run dashboard:build
alphaclaude app start
```

然后打开 `http://localhost:8800/dashboard`。

Dashboard 主要能力：

- 左侧可折叠/可拖宽侧边栏：盯盘、持仓、计划、成交、日志。
- 中间 K 线工作区：日K、周K、1分、5分、15分、60分，叠加 MA/EMA/BOLL 和 VOL。
- K 线缓存：本地缓存大小显示和手动清理按钮。
- 右侧可折叠/可拖宽 Agent 终端：浏览器内嵌 PowerShell，按钮切换 Claude Code / Codex CLI。

## Commands

引擎：

```bash
alphaclaude engine start --mode backtest --start 2024-01-01 --end 2024-06-30 -u default
alphaclaude engine start --mode paper -u default --daemon
alphaclaude engine list
alphaclaude engine status paper_2026-05-16T09-00-00
alphaclaude engine stop paper_2026-05-16T09-00-00
alphaclaude engine resume paper_2026-05-16T09-00-00 --daemon
alphaclaude engine stop-running
```

工具示例：

```bash
alphaclaude tools quote 600519
alphaclaude tools technical 600519 --all
alphaclaude tools backtest 600519 -s ma_cross
alphaclaude tools backtest_runner --start 2024-01-01 --end 2024-06-30 -u default
```

机器人常用指令：

| 指令 | 说明 |
|------|------|
| `帮助` | 显示指令列表 |
| `状态` | 查看运行健康、活跃 run、净值和风险摘要 |
| `状态 <run_id>` | 查询指定引擎运行 |
| `持仓` | 查看持仓明细、可卖/锁定、止损止盈 |
| `交易` | 查看最近成交、拒单和紧急动作 |
| `计划` | 查看今日盘前计划和风控规则 |
| `停止 <run_id>` | 私聊中停止指定引擎 |
| `恢复 <run_id>` | 私聊中恢复指定引擎；live 恢复保持观察/暂停语义 |
| `订阅` | 订阅每日推送 |
| `退订` | 取消订阅 |
| `/task <描述>` | 创建自定义定时任务 |
| `/tasks` | 列出定时任务 |
| `新对话` | 重置对话上下文 |

建议在飞书开发者后台把机器人菜单配置为上述中文指令；英文 slash 指令只作为兼容别名保留。配置示例见 [docs/feishu-bot-menu.md](docs/feishu-bot-menu.md)。

## Project Layout

- `src/alphaclaude/app/`: FastAPI 应用、Dashboard API/SSE/WebSocket、会话和指令编排。
- `src/alphaclaude/engine/`: 回测/模拟盘/预留 live 引擎核心。
- `src/alphaclaude/tools/`: 行情、技术、风控、报表等 CLI 工具。
- `src/alphaclaude/feishu/`: 飞书认证、群聊、用户和 WebSocket 适配。
- `dashboard/`: React + Vite + TypeScript Dashboard 源码。
- `scripts/`: Windows 开发启动脚本。
- `skills/`, `tests/`, `docs/`, `data/output/`: 策略技能、测试、文档和引擎输出。

## Verification

常用验证命令：

```powershell
npm run dashboard:test
npm run dashboard:build
python -m pytest -q
python -m compileall -q src\alphaclaude
```

## Documentation

- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Skills](docs/skills.md)
- [Project comparison](docs/project-comparison.md)
- [Feishu bot menu](docs/feishu-bot-menu.md)

## License

MIT © AlphaClaude
