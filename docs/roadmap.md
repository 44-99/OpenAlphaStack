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
- ✅ Public live start/resume paths removed; historical live runs are read-only.

## Next: MCP contract hardening

- ✅ Added explicit JSON schemas and version fields for plans and run snapshots.
- ✅ Added source freshness and provenance to every MCP market-data response.
- ✅ Added structured MCP errors without provider exception text.
- ✅ Added contract, Demo, run snapshot, and ledger resource URIs.
- ✅ Added a read-only synthetic Demo dataset and Skill forward-contract tests.
- Add dedicated report and strategy-metric resource URIs after their persisted schemas stabilize.

## Next: deterministic runtime

- ✅ Persist state, active plans, and ledger events in a per-run SQLite source of truth.
- ✅ Commit account mutations and matching ledger events atomically.
- ✅ Reject missing intraday data instead of synthesizing minute bars from daily OHLC.
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

- Remove the obsolete Agent-terminal screenshot and capture a verified current Dashboard image.
- Record a short Codex Scheduled task -> Skill -> MCP -> paper plan demo.
- Publish honest benchmark and failure reports rather than return promises.
- Publish plugin installation and scheduled-task composition examples.

## Live trading

Live trading remains unimplemented and inaccessible through the CLI and MCP. It requires a
separate BrokerAdapter, explicit human confirmation, order idempotency, restart
recovery, kill switches, authenticated deployment, and a new security review.
