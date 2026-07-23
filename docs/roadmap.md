# OpenAlphaStack roadmap

## Current architecture

- ✅ Project and package renamed from AlphaClaude to OpenAlphaStack.
- ✅ Codex Desktop selected as the primary Agent host.
- ✅ Repository packaged as an `open-alpha-stack` Codex plugin.
- ✅ Domain Skills retain the original market, screening, stock, and T0 taxonomy.
- ✅ Local stdio MCP server exposes typed market and paper-plan tools.
- ✅ MCP plan publication is paper-only, validated, idempotent, and atomic.
- ✅ Paper engine reloads externally published plans and stays observation-only without one.
- ✅ Embedded Agent terminal and in-process Agent scheduling removed from the main path.
- ✅ Backtests no longer invoke an Agent internally.

## Next: MCP contract hardening

- Add explicit JSON schemas and version fields for plans and run snapshots.
- Add source freshness and provenance to every market-data response.
- Add structured MCP errors rather than provider-specific exception text.
- Add resource URIs for plan, state, ledger, reports, and strategy metrics.
- Add a read-only demo dataset for Skill forward tests.

## Next: deterministic runtime

- Run the paper engine as a long-lived local service that idles outside exchange sessions.
- Make plan activation a first-class state transition with an audit event.
- Add plan expiry and automatic observation fallback.
- Add correlation, liquidity, suspension, limit-up/down, and stale-price gates.
- Preserve T+1, fees, idempotency, and append-only ledger invariants.

## Next: validation and economics

- Add walk-forward and untouched out-of-sample evaluation.
- Compare every Agent-assisted workflow against cash, index, and pure-rule baselines.
- Persist scheduled-run duration, failures, retries, and available usage metrics.
- Report trading P&L and Agent operating cost separately.
- Define stop conditions for strategies with persistent negative expectancy.

## Next: product and distribution

- Replace the old screenshot with a Dashboard image without the Agent terminal.
- Record a short Codex Scheduled task -> Skill -> MCP -> paper plan demo.
- Publish honest benchmark and failure reports rather than return promises.
- Publish plugin installation and scheduled-task composition examples.

## Live trading

Live trading remains unimplemented and inaccessible through MCP. It requires a
separate BrokerAdapter, explicit human confirmation, order idempotency, restart
recovery, kill switches, authenticated deployment, and a new security review.
