# AlphaClaude

AI stock trading bot powered by **Claude Code**. Daily A-share market analysis and stock recommendations delivered via Feishu (Lark).

## Features

- **Daily Reports** — 9:00 morning briefing, 12:00 midday update, 15:30 closing summary (weekdays)
- **Stock Screening** — Multi-factor short-term (1-5d) and mid-term (1-4w) picks via akshare
- **Interactive Chat** — Ask about stocks, market trends, or portfolio in Feishu DM/group
- **Custom Tasks** — `/task analyze Moutai every morning at 8am` — user-defined cron jobs with natural language
- **Cross-Group Query** — `/group <id> <query>` — query any registered group from DM
- **Dual-Layer Memory** — Claude Code transcripts + project memory files, auto-consolidated every 12h
- **Skill System** — `.md` files in `skills/` with YAML frontmatter triggers, hot-reloaded at startup
- **Subscription** — `/sub` `/unsub` `/status` — opt-in daily push per group

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Feishu (Lark)                       │
│              WebSocket long-connection                   │
└──────────────────────┬──────────────────────────────────┘
                       │ events
                       ▼
┌─────────────────────────────────────────────────────────┐
│                      main.py                             │
│  Message orchestration · Sessions · Commands · Skills    │
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

**Dependency graph** (no cycles):

```
main ──→ memory, claude, stock, scheduler, feishu
scheduler ──→ memory, claude, stock, feishu
memory ──→ claude, config
```

## Design Philosophy

**Alpha** = Knowledge Brain — multi-dimensional intelligence feeding the Agent:

| Source | Role |
|--------|------|
| Real-time market data (akshare) | Price, volume, turnover, sector flow |
| Tongdaxin formulas (skills) | Practitioner battle-tested strategies encoded as progressive-disclosure skills |
| News / research (RAG) | External information injected into context on demand |
| Model intrinsic knowledge | Pretraining financial concepts, valuation theory, market mechanics |

**Claude** = Execution Core — Claude Code CLI as local Agent:

- **Lighter than alternatives**: No server infrastructure, no process pools. `pip install` + a CLI binary is the entire runtime.
- **Strong coding + high cost-performance**: Claude Code + DeepSeek for orchestration and analysis.
- **Inherits everything**: Multi-turn conversation, tool orchestration, session management, MCP protocol — all built into Claude Code. We don't rebuild these.
- **General-purpose by default**: Beyond trading — programming help, writing, knowledge Q&A, cross-platform chat — any capability Claude Code has, AlphaClaude inherits.

This project focuses on equipping the Claude Code Agent with a "stock trading brain": Skills as strategy knowledge, Python scripts as data computation, Feishu as the communication channel.

### Why Stateless Scripts Instead of an MCP Server

Claude Code's built-in Bash tool is sufficient for all our tooling needs:

| Factor | Our approach |
|--------|-------------|
| **Tools** | Python scripts in project root + `skills/*/scripts/` |
| **Invocation** | Bash subprocess (Claude Code built-in) |
| **State** | Stateless — each call is independent, instant return |
| **Complexity** | Near zero ops overhead — no process management, no lifecycle, no callback infrastructure |

All our tools are lightweight Python functions (akshare HTTP calls, formula calculations, vector search). They're stateless, return instantly, and have no special requirements. Claude Code's Bash tool handles them natively — no need for a separate MCP server, process pool, or callback system.

When Phase 3 automated trading requires a dedicated signal monitoring process, it will be a single sidecar daemon, not a layered service mesh.

## Quick Start

### Prerequisites

- Python 3.10+
- [Claude Code](https://claude.ai/code) CLI installed
- Feishu (Lark) developer account

### Setup

```bash
git clone https://github.com/44-99/AlphaClaude.git
cd AlphaClaude
pip install -r requirements.txt
```

### Configuration

1. Create a Feishu app at [Feishu Open Platform](https://open.feishu.cn)
2. Enable **Bot** capability and add it to your app
3. Set event subscription to **WebSocket long-connection mode**
4. Subscribe to `im.message.receive_v1` event
5. Grant permissions: `im:message`, `im:message:read`, `im:message.group:read`
6. Copy `.env.example` to `.env` and fill in your credentials:

```bash
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_BOT_NAME=StockBot
FEISHU_BOT_OPEN_ID=ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Claude CLI
CLAUDE_CMD=C:\Users\YourName\AppData\Roaming\npm\claude.cmd
CLAUDE_TIMEOUT=120
```

### Run

```bash
python main.py
```

Starts on port 8800. Health check: `http://localhost:8800/health`

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health + WebSocket status |
| GET | `/subscribers` | List subscribed chat IDs |
| GET | `/sessions` | List active sessions |
| POST | `/trigger/now?session=morning` | Manually trigger a job (`morning`/`midday`/`closing`/`dream`) |

### Auto-start on Windows

Copy `start_bot.bat` to the Windows Startup folder for automatic launch on login.

## Bot Commands

| Command | Description |
|---------|-------------|
| `/help` | Show welcome message and command list |
| `/sub` / `订阅` | Subscribe to daily push |
| `/unsub` / `退订` | Unsubscribe from daily push |
| `/status` / `推送状态` | Check subscription status |
| `/task <description>` | Create custom cron task (e.g. `/task analyze Moutai every 8am`) |
| `/task delete <id>` | Delete a custom task |
| `/tasks` | List all custom tasks |
| `/group <id> <query>` | Cross-group query (DM only) |
| `/groups` | List registered groups |
| `/new` / `新对话` | Reset conversation context |

## Trading Strategy

| Type | Criteria |
|------|----------|
| **Short (1-5d)** | Gain 2-9%, turnover 3-20%, volume ratio >1.5, turnover >100M CNY |
| **Mid (1-4w)** | PE 0-50, PB 0-8, gain 1-7%, turnover 2-15%, sector momentum |

Every recommendation includes entry price, stop-loss, and take-profit targets.

## Memory System

Dual-layer architecture:

| Layer | Location | Managed by | Content |
|-------|----------|------------|---------|
| Claude Code transcripts | `~/.claude/projects/.../` | Claude Code | Full conversation history |
| Project memory files | `data/memory/user/` and `data/memory/group/` | Consolidation job | User profiles, preferences, topic summaries |

Consolidation runs at 3:17 and 15:17 daily, scanning transcripts modified in the last 12 hours and updating memory files. New sessions inject relevant memory on first message.

## Project Structure

```
AlphaClaude/
├── main.py          — Message orchestration, sessions, commands, skills, FastAPI
├── memory.py        — User/group memory system, transcript consolidation
├── claude.py        — Claude Code CLI wrapper
├── stock.py         — Market data (akshare), multi-factor screening
├── scheduler.py     — APScheduler cron jobs + dynamic task CRUD
├── config.py        — Environment variable loading
├── feishu/          — Feishu SDK integration
│   ├── auth.py      — Tenant access token
│   ├── bot.py       — send_text / send_post / reply_message / parse_event
│   ├── group.py     — Group membership check
│   ├── user.py      — User label lookup
│   └── ws.py        — lark-oapi WebSocket listener
├── tools/           — CLI tools for Claude Code (JSON in/out, stateless)
│   ├── quote.py     — Real-time quotes & market overview
│   ├── technical.py — Technical indicators (MA/MACD/RSI/KDJ/Bollinger)
│   ├── fundamental.py — PE/PB/ROE/revenue growth
│   ├── flow.py      — Capital flow, north-bound, institutional
│   ├── news.py      — Announcements, analyst reports, sentiment
│   ├── screen.py    — Pluggable multi-factor screening
│   └── backtest.py  — Historical pattern backtest
├── skills/          — Strategy frameworks with tool orchestration
│   └── example-stock-alert.md
├── strategies/      — Screening/backtest strategy configs
├── data/            — Runtime data (sessions, subscribers, tasks, memory, cache)
├── CLAUDE.md        — Claude Code project instructions
└── requirements.txt
```

## Skill System

Skills use **progressive disclosure** to keep the initial prompt lean while giving Claude Code access to deep domain knowledge on demand:

```
skills/ma-cross/
├── SKILL.md              # Router: triggers, when to use, which reference to load
├── references/
│   ├── golden-cross.md   # Golden cross buy signal: formula logic, parameters, stop-loss
│   └── death-cross.md    # Death cross sell signal: same structure
└── scripts/
    └── ma_signal.py      # Claude Code executes on demand: akshare → calculate crossover
```

- **SKILL.md** loads at startup (Claude Code injects into context). Acts as a router — _when_ to use this skill and _which_ reference file to read.
- **references/** loaded on demand. Contains formula theory, parameter rationale, market condition notes, tuning guides.
- **scripts/** executed by Claude Code via Bash. Python scripts that fetch data and compute signals.

## Future Work

### Phase 1: Tool Layer — Give Claude Code a Trading Workstation (P0)

Replace `stock.py`'s monolithic "fetch-and-scream" with discrete CLI tools. Each tool is a single-purpose script: JSON in, JSON out. Claude Code calls them via Bash, decides what to query and how to combine the results.

| ID | Tool | Command | Description |
|----|------|---------|-------------|
| 1.1 | `quote` | `python tools/quote.py 600519` | Real-time price, change%, volume, turnover. `--market` for index overview. |
| 1.2 | `technical` | `python tools/technical.py 600519 --all` | MA (5/10/20/60), MACD, RSI, KDJ, Bollinger Bands, volume-price analysis via pandas/ta-lib |
| 1.3 | `fundamental` | `python tools/fundamental.py 600519` | PE / PB / ROE / revenue growth / industry percentile ranking |
| 1.4 | `flow` | `python tools/flow.py 600519` | North-bound capital, institutional net flow, large-order direction |
| 1.5 | `news` | `python tools/news.py 600519` | Recent announcements, analyst reports, social sentiment aggregation |
| 1.6 | `screen` | `python tools/screen.py --strategy breakout` | Pluggable multi-factor screening. Each strategy is a config file in `strategies/`, not hard-coded thresholds. |
| 1.7 | `backtest` | `python tools/backtest.py 600519 --strategy ma_cross` | Lightweight historical backtest. "This setup appeared N times, win rate X%, avg return Y%." |
| 1.8 | `watch_001` | `python tools/watch_001.py` | User watchlist management: add/remove/list stocks, query portfolio P&L |

**Why CLI scripts instead of an MCP server**: Same design philosophy as the rest of the project — each call is stateless, returns instantly, and Claude Code's built-in Bash tool handles invocation natively. Zero infrastructure overhead.

### Phase 2: Strategy Skills + Credibility Loop (P0/P1)

| ID | Feature | Description |
|----|---------|-------------|
| 2.1 | **Skill System Upgrade** | Skills evolve from keyword→prompt injection to **strategy frameworks with tool orchestration**. A skill declares which tools to call and in what order (e.g., volume breakout = `quote` → `technical` for volume ratio → `flow` for confirmation → output decision). YAML frontmatter gains a `tools:` field and `references/` for strategy theory. |
| 2.2 | **Trade Tracking** | `/track <code> <entry> <stop-loss> <take-profit>` records every recommendation. Daily cron compares targets against actual prices. `/track status` shows cumulative win rate, P&L, and Sharpe ratio. **This is the foundation of credibility** — without it, the bot is just another bullshit generator. |
| 2.3 | **Rich Report Cards** | Daily 15:30 closing summary uses Feishu `send_post` card format: gainers in green, losers in red, win-rate summary, track-record badge. Cards are screenshot-friendly — users share them into other groups, driving organic growth. |
| 2.4 | **One-Click Deploy** | Docker Compose + pre-configured `.env` template. Target: a non-programmer goes from zero to running bot in under 5 minutes. Feishu App Store listing once stable. |
| 2.5 | **Watchlist System** | `/watch 600519` `/unwatch` `/portfolio` — user-curated stock list. Intraday alerts when a watched stock crosses a user-defined price threshold. |
| 2.6 | **Reliability Hardening** | Process supervisor (systemd / Docker restart policy), structured logging, Feishu alert on crash. Session queuing instead of rejecting concurrent messages with "busy, try later." |

### Phase 3: Trading Pipeline (P1/P2)

| ID | Feature | Description |
|----|---------|-------------|
| 3.1 | **Broker Integration** | `tools/trade.py` wraps broker API. Start with Eastmoney OpenAPI (low barrier, RESTful, retail-friendly). JSON order spec: `{symbol, action, quantity, price, order_type}`. QMT/PTrade as premium options later. |
| 3.2 | **Trade Confirmation Flow** | Every order triggers a Feishu interactive card: "*About to BUY 贵州茅台 100 shares @ ¥1850. Confirm?*" User taps "Confirm" → order executes → fill notification. No silent auto-trading. |
| 3.3 | **Strategy → Signal → Trade Pipeline** | End-to-end: skill framework (Phase 2.1) triggers tool chain → Claude Code produces structured trade signal → Feishu confirmation card → `trade.py` executes → P&L tracked (Phase 2.2). Human always in the loop. |
| 3.4 | **Conditional Orders** | Stop-loss and take-profit orders persisted in `data/orders.json`. Dedicated monitor process checks prices every 30s, triggers order if conditions met. Survives bot restart. |

### Phase 4: Real-Time Intelligence (P2)

| ID | Feature | Description |
|----|---------|-------------|
| 4.1 | **Intraday Alert Engine** | Dedicated process polls akshare every 30-60s. Detects: price breakouts, volume surges, limit-up/down approach, index turning points. Pushes Feishu card to subscribers. |
| 4.2 | **Backtest Reports** | `tools/backtest.py` upgraded with: parameter optimization (grid search), Monte Carlo simulation, sector-specific benchmarks. Output: win rate, Sharpe, max drawdown, profit factor. |
| 4.3 | **News Sentiment Pipeline** | Scheduled scraping of financial news → lightweight sentiment classification → injected into session context when user queries related stocks. No heavy vector DB — keyword-indexed JSON cache. |

## License

MIT © AlphaClaude
