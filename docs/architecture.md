# OpenAlphaStack Architecture v4

## Positioning

OpenAlphaStack is an open-source Codex plugin for A-share research, backtesting,
paper execution, and audit. It packages MCP and domain Skills while keeping the
Python runtime provider-neutral. It does not embed or spawn an Agent CLI.

## Components

| Layer | Owner | Responsibilities |
|---|---|---|
| Agent host | Codex Desktop | Threads, scheduled tasks, reasoning, operator review |
| Plugin | `.codex-plugin/plugin.json` | Skills and MCP discovery |
| Workflow | Domain Skills | Market, screening, stock, and T0 analysis contracts |
| Protocol | `openalphastack` MCP | Typed reads and bounded paper mutations |
| Domain | Python package | Data, risk, plans, state, ledger, backtests |
| Runtime | Paper engine | Calendar-aware idle, hot plan reload, mechanical execution |
| UI | Dashboard | Read-oriented K-line, account, workflow, and audit views |

## Control flow

```text
Scheduled premarket task
  -> compose market-analyzer + stock-screener + stock-analyzer
  -> read market/run data through MCP
  -> validate_paper_plan
  -> save_plan_draft
  -> publish_paper_plan (paper only, idempotent, optimistic concurrency)
  -> paper engine refreshes the newer validated plan from run.sqlite3
  -> FastLane applies deterministic rules
  -> atomically commit account state + ledger event to run.sqlite3
  -> refresh human-readable JSON/JSONL projections and workflow events
  -> scheduled postclose prompt reviews facts without mutation
```

## Boundary rules

- Skills may propose; Python validates.
- MCP may publish paper plans; it may not place live orders.
- The engine may execute a valid current plan; it may not call a model.
- Emergency handling is deterministic and notification-based.
- A missing, stale, or invalid plan produces observation mode.
- SQLite is the per-run source of truth; JSON and JSONL are projections.
- Account mutations and their ledger events commit in one SQLite transaction.
- Missing intraday bars fail closed; backtests never synthesize minute bars from daily OHLC.
- Public engine modes are paper and backtest only. Historical live runs are read-only.

## MCP surface

The stdio server is started with `openalphastack mcp serve` and configured in
`.codex/config.toml`.

Read and calculation groups:

- market overview, quote, technical, fundamental, and news
- deterministic candidate screens and baseline backtests
- paper/backtest run snapshots and ledger tails
- volatility and position sizing
- plan validation

Mutation group:

- save a non-executable plan draft
- atomically publish a validated paper plan

Every publication requires an idempotency key. Optional `expected_updated`
provides optimistic concurrency against a plan changed since the Agent read it.

### Versioned response contract

All MCP tools return the `openalphastack.mcp/v1` envelope. Consumers check `ok`
before reading `data`; failures contain a stable `error.code`, retryability and
non-sensitive details. Market responses expose `meta.source`, `meta.as_of` and
`meta.freshness`. Plans and run snapshots use the separate
`openalphastack.plan/v1` and `openalphastack.run-snapshot/v1` contracts.

Resources:

- `openalphastack://contracts/v1`
- `openalphastack://demo/catalog`
- `openalphastack://demo/{dataset}`
- `openalphastack://runs/{run_id}/snapshot`
- `openalphastack://runs/{run_id}/ledger`

The bundled Demo datasets are static synthetic fixtures. They support offline
Skill verification and cannot mutate or publish a trading plan. Dashboard Demo
account, plan, and ledger fixtures share the same `demo_data` ownership boundary;
chart and workflow fixtures remain explicitly UI-only presentation data.

## Persistence boundary

Every paper or backtest run owns a `run.sqlite3` database containing runtime
state, the active validated plan, and append-only ledger events. SQLite WAL,
full synchronous commits, and immediate write transactions provide the
cross-record atomicity boundary. `state.json`, `plan.json`, and `ledger.jsonl`
exist for operator inspection and compatibility; corrupt or stale projections
must not override a valid database.

## Scheduling boundary

Codex scheduled tasks compose the domain Skills for premarket research,
postclose review, or periodic evaluation. Those time-based recipes belong in
the task prompt rather than separate Skills. Scheduled tasks are not the
real-time trading clock; Python owns intraday timing and execution.

## Dashboard boundary

The Dashboard no longer exposes PowerShell, Claude Code, or Codex terminal
WebSockets. Workflow prompts can be copied into Codex Desktop, but the browser
cannot execute arbitrary local commands.

## Deployment

The default bind address is `127.0.0.1`. Remote deployment requires a separate
authenticated reverse proxy and security review. The current MCP mutation
contract remains paper-only regardless of network configuration.
