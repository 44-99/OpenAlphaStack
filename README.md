<div align="center">

<h1>OpenAlphaStack</h1>

<p><strong>An open-source Codex plugin stack for auditable A-share research, backtesting, and paper trading.</strong></p>

[![CI](https://github.com/44-99/OpenAlphaStack/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/44-99/OpenAlphaStack/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-2563eb.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab.svg?logo=python&logoColor=white)](pyproject.toml)
[![Codex Plugin](https://img.shields.io/badge/Codex-plugin-111827.svg)](.codex-plugin/plugin.json)
[![MCP](https://img.shields.io/badge/MCP-local--first-1f9d8a.svg)](.mcp.json)
[![GitHub stars](https://img.shields.io/github/stars/44-99/OpenAlphaStack?style=flat&logo=github)](https://github.com/44-99/OpenAlphaStack/stargazers)

[Quick start](#quick-start) · [Architecture](docs/architecture.md) · [Skills](docs/skills.md) · [Roadmap](docs/roadmap.md)

</div>

The plugin packages domain Skills and a local MCP server as one installable
system. Codex Desktop may compose those Skills in scheduled tasks. The MCP
server exposes typed market, risk, backtest, and paper-plan tools. Python validates
plans and executes them mechanically; it never launches an Agent or invents a
missing trading plan.

> Paper trading only. OpenAlphaStack does not place real orders and does not promise investment returns.

## Why OpenAlphaStack?

- **One plugin, two extension layers** — package reusable domain Skills and a
  typed MCP server as one Codex-native system.
- **Agent research, deterministic execution** — Codex analyzes and proposes;
  Python owns validation, T+1 rules, fees, state transitions and paper execution.
- **Auditable by design** — explicit plans, version checks, idempotency keys,
  append-only ledgers and observable workflow events.
- **Local-first and model-independent at the core** — market tools and execution
  state stay local instead of being hidden inside an Agent subprocess.
- **Honest safety boundary** — all mutation tools are paper-only; there is no
  live-order MCP tool.

## Architecture

```text
Codex task or scheduled prompt
        │ invokes
        ▼
Domain Skills ─────────► OpenAlphaStack MCP
                              │
                 ┌────────────┼─────────────┐
                 ▼            ▼             ▼
             Market data   Plan + risk   Backtests
                              │
                              ▼
                    Deterministic paper engine
                              │
                    plan / state / ledger
                              │
                              ▼
                       Read-only Dashboard
```

The boundaries are intentional:

- **Codex Desktop**: conversations, scheduled tasks, research, and operator review.
- **Skills**: reusable market, screening, stock-analysis, and T0 domain capabilities.
- **MCP**: typed access to live data and bounded paper-only actions.
- **Python engine**: T+1 rules, fees, validation, state, audit, and mechanical execution.
- **Dashboard**: K-line, plans, positions, ledger, workflow events, and diagnostics.

## Quick start

Requirements:

- Python 3.10+
- Node.js 20+
- Codex Desktop

```powershell
git clone https://github.com/44-99/OpenAlphaStack.git
cd OpenAlphaStack
pip install -e .
openalphastack doctor
```

The base install supports the Codex plugin, MCP contracts, and offline Demo path.
Install only the surfaces you use:

```powershell
pip install -e ".[market]"             # AkShare-backed market providers
pip install -e ".[engine]"             # paper/backtest Parquet runtime
pip install -e ".[dashboard]"          # FastAPI Dashboard
pip install -e ".[all]"                # complete local development runtime

npm install
npm run dashboard:build
openalphastack doctor
openalphastack app start
```

Open `http://127.0.0.1:8800/dashboard`.

Then open the repository in Codex Desktop and try:

```text
Use $market-analyzer to assess today's A-share market, cite the MCP data used,
and finish with risks and invalidation conditions.
```

### Offline first run

Market providers may be unavailable outside trading hours or behind a restricted
network. To verify the complete Skill → MCP path without treating sample values
as market facts, ask Codex:

```text
Use $market-analyzer in offline demo mode. Read the market_overview and
market_news demo datasets, show their schema version, source, as-of time and
freshness status, then produce a short report clearly labelled as synthetic data.
```

The `read_demo_dataset` MCP tool is read-only and deterministic. Skills must not
publish Demo-derived values into a paper plan. Available datasets cover market
overview, screening, quote, technical, fundamentals, news and a baseline backtest.

The repository is also a Codex plugin: `.codex-plugin/plugin.json` discovers the
Skills and `.mcp.json` registers the stdio server. After installing the Python
package, install/open the plugin in Codex and verify the `open-alpha-stack` MCP
tools are available.

Start the MCP server manually for diagnostics:

```powershell
openalphastack mcp serve
```

Check the local installation at any time:

```powershell
openalphastack doctor
openalphastack doctor --json
```

## Codex Skills

- `$market-analyzer`: market environment, sentiment, sectors, and leaders.
- `$stock-screener`: deterministic screening and candidate verification.
- `$stock-analyzer`: technical, fundamental, news, position, and risk analysis.
- `$t0-intraday`: T0 feasibility, direction, sizing, and guardrails.

Scheduled tasks compose these domain Skills; premarket and postclose are task
prompts, not duplicate Skills. A local scheduled task requires the computer and
Codex Desktop to remain running.

## MCP safety contract

Read tools expose paper/backtest runs, market data, indicators, news, screens,
risk calculations, and deterministic baseline backtests.

Every MCP tool returns a versioned envelope:

```json
{
  "schema_version": "openalphastack.mcp/v1",
  "ok": true,
  "data": {},
  "meta": {
    "source": "provider-or-demo-dataset",
    "as_of": "2026-07-23T10:00:00+08:00",
    "freshness": {"status": "fresh"},
    "demo": false
  }
}
```

Callers must check `ok` before reading `data`. Failures use a stable
`error.code` and do not expose provider exception text. JSON schemas are
available through `get_contracts` and `openalphastack://contracts/v1`.

The only plan mutations are:

1. `save_plan_draft` — writes a non-executable `plan.codex-draft.json`.
2. `publish_paper_plan` — validates, requires an idempotency key, checks the
   expected plan version, and atomically updates a paper run only.

There is no public live mode: the CLI cannot start or resume one, and MCP has no
live-order tool. Historical `live_*` directories remain read-only for migration
and audit. No shell or arbitrary file-write tool is exposed.

## Engine commands

```powershell
openalphastack engine start --mode paper -u default --daemon
openalphastack engine list
openalphastack engine status <run_id>
openalphastack engine stop <run_id>
openalphastack engine resume <run_id> --daemon

openalphastack engine start --mode backtest \
  --start 2024-01-01 --end 2024-06-30 -u default
```

The paper engine can stay running outside trading hours. It idles according to
the trading calendar and remains observation-only until Codex publishes a valid
plan for the current date.

Each run uses `run.sqlite3` as the transactional source of truth for account
state, the active plan, and ledger events. `state.json`, `plan.json`, and
`ledger.jsonl` are human-readable projections. A trade updates account state and
its matching ledger event in one SQLite transaction.

Backtests require real cached or provider minute bars. Missing intraday data now
fails closed instead of fabricating bars from daily OHLC. Backtest output remains
experimental evidence—not a profitability claim—until walk-forward,
out-of-sample, and baseline comparisons are implemented.

## Verification

```powershell
npm run dashboard:test
npm run dashboard:build
python -m pytest -q
python -m compileall -q src\openalphastack
```

## Documentation

- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Skills](docs/skills.md)
- [Feishu notifications](docs/feishu-bot-menu.md)

## Contributing

Issues and focused pull requests are welcome. Before changing the repository,
read [AGENT_GUIDE.md](AGENT_GUIDE.md), preserve the paper-only MCP boundary and
add tests for behavior that affects validation, state, risk or idempotency.

## Security

The Dashboard binds to localhost by default. Do not expose it directly to a LAN
or the internet without adding authentication, TLS, CSRF protection, and an
explicit network policy.

## License

MIT © OpenAlphaStack
