# AlphaClaude

AI stock trading bot powered by **Claude Code**. Daily A-share market analysis and stock recommendations delivered via Feishu (Lark).

## Features

- **Daily Reports** — 9:00 morning briefing, 12:00 midday update, 15:30 closing summary (weekdays)
- **Stock Screening** — Multi-factor short-term (1-5d) and mid-term (1-4w) picks via akshare
- **Interactive Chat** — Ask about stocks, market trends, or portfolio in Feishu DM/group
- **Custom Tasks** — `/task 每天早上8点分析茅台` — user-defined cron jobs with natural language
- **Cross-Group Query** — `/group <群ID> <提问>` — query any registered group from DM
- **Dual-Layer Memory** — Claude Code transcripts + project memory files, auto-consolidated every 12h
- **Skill System** — `.md` files in `skills/` with YAML frontmatter triggers, hot-reloaded at startup
- **Subscription** — `/sub` `/unsub` `/status` — opt-in daily push per group

## Architecture

```
Feishu WebSocket (long-connection, lark-oapi SDK)
    ↕
FastAPI + APScheduler
    ├── main.py        — Message orchestration, session persistence, commands
    ├── memory.py      — User/group profiles, transcript consolidation
    ├── claude.py      — Claude Code CLI wrapper (--resume / --session-id)
    ├── stock.py       — Market data via akshare (cached, multi-factor screening)
    ├── scheduler.py   — Cron jobs + dynamic task management
    ├── config.py      — Environment variables
    ├── feishu/        — Auth, messaging, event parsing, WebSocket client
    │   ├── auth.py    — Tenant access token
    │   ├── bot.py     — send_text / send_post / reply_message / parse_event
    │   ├── group.py   — Group membership check
    │   ├── user.py    — User label lookup
    │   └── ws.py      — lark-oapi WebSocket listener
    └── skills/        — Stock alert .md skill files
```

**Dependency graph** (no cycles):

```
main → memory, claude, stock, scheduler, feishu
scheduler → memory, claude, stock, feishu
memory → claude, config
```

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
2. Enable **Bot** capability, add it to your app
3. Set event subscription to **WebSocket long-connection mode**
4. Subscribe to `im.message.receive_v1` event
5. Grant permissions: `im:message`, `im:message:read`, `im:message.group:read`
6. Copy `.env.example` to `.env` and fill in your credentials:

```bash
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_BOT_NAME=股票助手
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
| POST | `/trigger/now?session=morning` | Manually trigger a scheduled job (`morning`/`midday`/`closing`/`dream`) |

### Auto-start on Windows

Copy `start_bot.bat` to the Windows Startup folder for automatic launch on login.

## Bot Commands

| Command | Description |
|---------|-------------|
| `/help` | Show welcome message and command list |
| `/sub` or `订阅` | Subscribe to daily push |
| `/unsub` or `退订` | Unsubscribe |
| `/status` or `推送状态` | Check subscription status |
| `/task <描述>` | Create custom cron task (e.g. `/task 每天早上8点分析茅台`) |
| `/task delete <id>` | Delete a custom task |
| `/tasks` | List all custom tasks |
| `/group <群ID> <提问>` | Cross-group query (DM only) |
| `/groups` | List registered groups |
| `/new` or `新对话` | Reset conversation context |

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
│   ├── auth.py
│   ├── bot.py
│   ├── group.py
│   ├── user.py
│   └── ws.py
├── skills/          — Stock alert .md skills
├── data/            — Runtime data (sessions, subscribers, tasks, memory, cache)
├── CLAUDE.md        — Claude Code project instructions
└── requirements.txt
```

## License

MIT © AlphaClaude
