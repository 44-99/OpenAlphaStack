<div align="center">

<h1>OpenAlphaStack</h1>

<p><strong>An open-source Codex plugin stack for auditable A-share research, backtesting, and paper trading.</strong></p>

[![CI](https://github.com/44-99/OpenAlphaStack/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/44-99/OpenAlphaStack/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-2563eb.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab.svg?logo=python&logoColor=white)](pyproject.toml)
[![Codex Plugin](https://img.shields.io/badge/Codex-plugin-111827.svg)](.codex-plugin/plugin.json)
[![MCP](https://img.shields.io/badge/MCP-local--first-1f9d8a.svg)](.mcp.json)

[简体中文](README.md) · [Architecture](docs/architecture.md) · [Skills](docs/skills.md) · [Roadmap](docs/roadmap.md)

</div>

OpenAlphaStack packages A-share domain Skills and a typed local MCP server as a
Codex plugin. Codex Desktop owns conversations, research and schedules. Python
owns deterministic validation, T+1 rules, fees, account state and paper
execution.

The canonical product interface and documentation are in Simplified Chinese.
This concise page exists for overseas Chinese users, developers researching
China's A-share market, and MCP/Codex tool builders evaluating the architecture.

> Research, backtesting and paper trading only. OpenAlphaStack does not place
> real orders or promise investment returns.

## What it provides

- Codex Skills for market analysis, screening, stock analysis and T0 research.
- Typed MCP tools for market data, risk calculations, backtests and paper plans.
- A deterministic Python paper engine with SQLite state and append-only audit
  projections.
- A local FastAPI + React Dashboard for A-share search, K-lines, plans,
  positions, ledger events and the Research → Execution → Evaluation workflow.
- A single-Agent default: Skills are composed on demand without mandatory
  sub-agent orchestration.

## Architecture

```text
Codex task / schedule
        │
Domain Skills ───────► typed local MCP
                            │
                 market / risk / backtest
                            │
                 deterministic paper engine
                            │
                  SQLite + audit projections
                            │
                      local Dashboard
```

## Quick start

Requirements: Python 3.10+, Node.js 20+, and Codex Desktop.

```powershell
git clone https://github.com/44-99/OpenAlphaStack.git
cd OpenAlphaStack
pip install -e ".[all]"
npm install
npm run dashboard:build
openalphastack doctor
openalphastack app start
```

Open `http://127.0.0.1:8800/dashboard`, then open the repository in Codex
Desktop and invoke one of the packaged Skills.

For the full setup, safety contract, engine commands and verification workflow,
use the canonical [Simplified Chinese README](README.md).

## License

MIT © OpenAlphaStack
