# AlphaClaude

AI stock trading bot powered by **Claude Code**. Get daily A-share market analysis and stock recommendations via Feishu (Lark).

## Features

- **Daily Reports** — 9:00 morning briefing, 12:00 midday update, 15:30 closing summary (weekdays)
- **Stock Screening** — Multi-factor short-term (1-5 days) and mid-term (1-4 weeks) picks
- **Interactive Chat** — Ask about specific stocks, market trends, or portfolio in Feishu DM/group
- **Real-time Data** — Powered by [akshare](https://github.com/akfamily/akshare), covers A-share indices, sectors, hot stocks

## Architecture

```
Feishu WebSocket (long-connection)
    ↕
FastAPI + APScheduler
    ├── feishu/  — Auth, messaging, event handling
    ├── stock/   — Market data via akshare
    ├── claude/  — Claude Code CLI wrapper
    └── scheduler/ — Cron tasks (9:00 / 12:00 / 15:30)
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
2. Enable **Bot** capability
3. Set event subscription to **WebSocket long-connection mode**
4. Subscribe to `im.message.receive_v1` event
5. Grant permissions: `im:message`, `im:message:read`
6. Copy `.env.example` to `.env` and fill in your credentials:

```bash
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Run

```bash
python main.py
```

The bot starts on port 8800. Health check: `http://localhost:8800/health`

### Auto-start on Windows

`start_bot.bat` is copied to the Windows Startup folder for automatic launch on login.

## Trading Strategy

| Type | Criteria |
|------|----------|
| **Short (1-5d)** | Gain 2-9%, turnover 3-20%, volume ratio >1.5, turnover >100M CNY |
| **Mid (1-4w)** | PE 0-50, PB 0-8, gain 1-7%, turnover 2-15%, sector momentum |

Every recommendation includes entry price, stop-loss, and take-profit targets.

## License

MIT © AlphaClaude
