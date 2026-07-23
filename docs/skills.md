# Domain Skills

OpenAlphaStack keeps Skills aligned with reusable analysis capabilities instead
of execution time. Codex tasks and scheduled prompts compose them as needed.

| Skill | Responsibility | Primary MCP tools |
|---|---|---|
| `market-analyzer` | Market, sentiment, sector, and leader analysis | `market_overview`, `market_news` |
| `stock-screener` | Deterministic screening and candidate verification | `screen_candidates`, quote, technical, news |
| `stock-analyzer` | Evidence-backed analysis of one stock | quote, technical, fundamentals, news, risk |
| `t0-intraday` | T0 feasibility and guardrails for existing holdings | quote, technical, position sizing |

## Scheduled-task composition

A premarket task normally composes `market-analyzer`, `stock-screener`, and
`stock-analyzer`, then uses the plan MCP tools directly to validate and save a
draft. Publication must be explicitly requested and remains paper-only.

A postclose task reads the run snapshot and immutable ledger directly, then may
invoke `market-analyzer` or `stock-analyzer` for attribution. Strategy and cost
reviews are scheduled prompts or ordinary Codex tasks, not separate Skills.

Validate every Skill with the `skill-creator` `quick_validate.py` script after
editing it.
