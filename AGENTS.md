# OpenAlphaStack Agent Instructions

OpenAlphaStack is a Codex Desktop plugin for auditable A-share research and
paper trading. Repository Skills define research workflows, bounded MCP tools
expose data and domain actions, and Python owns deterministic execution.

## Architecture boundaries

- Do not launch Codex, Claude Code, another Agent, or an LLM API from the application.
- Put Agent workflow instructions in `skills/` and live capabilities behind typed MCP tools.
- Keep validation, T+1, fees, risk limits, state transitions, idempotency, backtests,
  and mechanical paper execution in Python.
- Public engine modes are paper and backtest only. MCP mutations are paper-only;
  historical live runs remain read-only.
- Missing or stale plans keep the engine in observation mode. Never invent a plan.
- The Dashboard is an observability surface, not an Agent terminal.
- Use one Codex Agent by default. Do not spawn or require subagents unless the user
  explicitly requests independent parallel work.
- Confidence and narrative reasoning are audit metadata, never execution gates.

## Safety and privacy

- Bind local services to `127.0.0.1` by default and never expose arbitrary shell execution.
- Do not commit `.env`, runtime data under `data/`, secrets, or private Feishu content.
- Treat per-run SQLite as canonical; JSON/JSONL files are human-readable projections.
- Preserve atomic state-plus-ledger commits and append-only ledger semantics.

## Development workflow

- Preserve unrelated user changes. Commit or push only when the user explicitly requests it.
- Add tests for changed MCP validation, paper-only boundaries, idempotency, and state reloads.
- Run the smallest relevant checks first, then the available baseline:

```powershell
npm run dashboard:test
npm run dashboard:build
python -m pytest -q
python -m compileall -q src\openalphastack
python -X utf8 C:\Users\Admin\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\market-analyzer
```
