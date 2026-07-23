# OpenAlphaStack

OpenAlphaStack is an open-source Codex plugin for auditable A-share research and
paper trading. Domain Skills provide reusable analysis, scheduled prompts compose
them, and the `open-alpha-stack` MCP server exposes bounded tools. Python owns
validation, state, risk rules, backtests, and mechanical paper execution.

## Architecture rules

1. Do not launch Claude Code, Codex CLI, or any other Agent subprocess from the application.
2. Do not call an LLM API from the engine or tools package.
3. Put Agent workflow instructions in `skills/`.
4. Put live data and domain actions behind typed MCP tools.
5. Keep risk limits, T+1 behavior, fees, state transitions, and idempotency in Python.
6. MCP mutations are paper-only. Never expose a live-order tool before an explicit safety design and separate approval.
7. Missing or stale plans keep the engine in observation mode; the engine must not invent a plan.
8. The Dashboard is an observability surface, not an Agent terminal.

## Safety and privacy

- Bind the local service to `127.0.0.1` by default.
- Do not expose arbitrary shell execution through HTTP, WebSocket, or MCP.
- Do not commit `.env` or runtime data under `data/`.
- Do not reveal private Feishu conversations or local secrets.
- Preserve atomic writes and append-only ledgers.

## Development rules

- Do not commit or push unless the user explicitly requests it.
- Preserve unrelated user changes.
- Add tests for MCP validation, paper-only boundaries, idempotency, and state reloads.
- Run the smallest relevant checks first, then the full verification baseline when dependencies are available.

## Verification

```powershell
npm run dashboard:test
npm run dashboard:build
python -m pytest -q
python -m compileall -q src\openalphastack
python C:\Users\Admin\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\market-analyzer
```
