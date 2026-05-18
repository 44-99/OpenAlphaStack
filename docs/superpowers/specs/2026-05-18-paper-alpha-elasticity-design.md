# Paper Alpha Elasticity Design

Status: design approved for implementation planning.

## Context

The current paper run proves the engine can operate, but the trading outcome is weak. The problem is not one losing symbol. The recent ledger and plan show a pattern:

- A bearish market still allowed multiple high-volatility, limit-up-style candidates.
- Shadow diagnostics flagged bearish-day over-entry, too many daily trades, and weak payoff symmetry.
- Intraday emergency handling has been noisy; Python rules should own mechanical exits, while Claude should focus on higher-value judgment.

The goal of this pass is to improve upside elasticity without turning the system into blind chasing. The paper engine should still be conservative enough to debug and compare, but the alpha path should come from stronger momentum handling and better entry timing.

## Goals

- Preserve high-upside A-share momentum opportunities.
- Stop buying breakout names immediately at the open without confirmation.
- Add a pullback path so the system can enter strong trends after controlled retracement.
- Make position size and exit logic depend on strategy type.
- Reduce low-value intraday Claude emergency calls.
- Keep the behavior testable in backtest and paper mode.

## Non-Goals

- Do not implement live trading.
- Do not add a large UI or dashboard.
- Do not replace the current engine loop.
- Do not make all strategy decisions deterministic; Claude still owns pre-market plan intent.

## Strategy Types

Each `buy_candidate` should include a `strategy_type`:

| Type | Meaning | Auto Buy |
|------|---------|----------|
| `breakout` | Limit-up, volume breakout, hot-money momentum | Only after intraday strength confirmation |
| `pullback` | Strong trend retracing to MA/VWAP/support | Only inside pullback zone and without breakdown |
| `defensive` | Lower-volatility hedge or cash-like defensive equity | Small direct execution allowed |
| `watch_only` | Interesting but not safe for automatic execution | Never auto-buy |

Existing candidates without `strategy_type` should default conservatively based on source/volatility:

- High volatility, limit-up, or source B -> `breakout`
- MA trend with controlled retracement -> `pullback`
- Low beta/high dividend -> `defensive`
- Ambiguous or missing required fields -> `watch_only`

## Plan Contract

The pre-market plan should continue to output `plan.json`, but candidates need extra fields:

```json
{
  "code": "300000",
  "strategy_type": "breakout",
  "position_pct": 10,
  "probe_position_pct": 4,
  "entry_min": 12.30,
  "entry_max": 12.80,
  "confirm_after": "09:45",
  "confirm_rules": {
    "min_change_pct": 2.0,
    "max_open_gap_pct": 6.0,
    "must_hold_vwap": true,
    "must_hold_open": true
  },
  "invalid_rules": {
    "break_open_pct": -1.5,
    "break_vwap": true,
    "max_intraday_drawdown_pct": 4.0
  },
  "trailing_take_profit": {
    "activate_pct": 6.0,
    "trail_pct": 3.0
  }
}
```

For `pullback`, the plan should include pullback support:

```json
{
  "strategy_type": "pullback",
  "pullback_zone": [18.20, 18.80],
  "support_price": 18.00,
  "trend_ma": "MA10",
  "volume_contract_required": true
}
```

## Intraday Execution

FastLane should route candidates by strategy type.

### Breakout

Do not buy before `confirm_after`, default `09:45`.

A breakout entry requires:

- Current price still within or near entry bounds.
- Price holds open and VWAP when configured.
- Intraday drawdown from high is not excessive.
- Market is not in hard risk-off state.

Entry should be staged:

- First fill uses `probe_position_pct`, default 3%-5%.
- Add-on to full `position_pct` only if the symbol continues to hold confirmation after a second check.
- If add-on logic is not implemented in the first pass, only probe entry is allowed.

### Pullback

A pullback entry requires:

- Price enters `pullback_zone`.
- Price remains above `support_price`.
- Recent volume is not panic expansion.
- Broader market is not triggering risk-off lockout.

This is meant to catch second-chance entries in strong trends rather than chase the prior day limit-up close.

### Defensive

Defensive candidates can execute directly, but with smaller size and lower expected return:

- Max single position 5%-8%.
- No add-on.
- Stop and take-profit remain simple unless the plan provides better rules.

### Watch Only

Watch-only candidates are included in `/计划`, but never auto-bought. They can be used for later analysis and manual review.

## Position Sizing

Introduce elastic sizing:

- Unconfirmed probe: 3%-5%.
- Confirmed breakout: 8%-12%.
- Pullback: 6%-10%.
- Defensive: 5%-8%.
- Same theme/sector high-elasticity exposure: max 2 symbols.
- Daily new positions: default max 3.

If these caps conflict with existing `max_single_position_pct` or `max_total_position_pct`, the stricter cap wins.

## Exits

Exits should be strategy-aware.

### Breakout Exit

Exit or reduce when:

- Price breaks VWAP or open-line invalidation.
- Intraday drawdown from high exceeds configured threshold.
- Hard stop is hit.

Activate trailing take profit after 6%-8% unrealized gain. The first pass can implement this as a simple peak-based trailing stop.

### Pullback Exit

Exit when:

- Price breaks support or MA10 equivalent.
- Hard stop is hit.
- Take-profit target or trailing stop is hit.

### Defensive Exit

Keep current stop/take-profit logic unless the plan includes specific adjustments.

## Claude vs Python Boundary

Claude should:

- Classify strategy type pre-market.
- Explain why a candidate deserves breakout/pullback/defensive/watch-only treatment.
- Provide entry bounds, invalidation rules, and rough payoff assumptions.

Python should:

- Decide whether intraday confirmation is met.
- Enforce daily trade limits, position caps, risk-off lockout, and T+1 rules.
- Execute hard stops and trailing stops.
- Log rejected entries with deterministic reasons.

Emergency Claude should be reserved for higher-value situations:

- Multiple symbols in the same theme are moving together.
- A profitable breakout is deciding between trailing hold vs reduce.
- A market-wide regime shift is detected.

It should not be called repeatedly just because one holding crossed a mechanical loss threshold.

## Data Flow

1. `OvernightPipeline` produces candidates with `strategy_type` and execution metadata.
2. `PlanManager` normalizes missing fields and defaults ambiguous candidates to `watch_only`.
3. `FastLane` splits candidates into breakout, pullback, defensive, and watch-only queues.
4. Intraday quotes are evaluated against strategy-specific confirmation/invalidation rules.
5. `ExecutionEngine` remains the order validation and state mutation layer.
6. `Ledger` records both executed and rejected decisions with `strategy_type` and rejection reason.
7. `/计划`, `/交易`, and future reports show strategy type so paper results can be reviewed by class.

## Error Handling

- Missing `strategy_type`: infer conservatively or mark `watch_only`.
- Missing VWAP/open data: do not execute breakout confirmation that requires it.
- Missing pullback zone: mark pullback candidate as `watch_only`.
- Bad stop/take-profit direction: reject before order.
- Too many same-day new positions: reject and log.
- Candidate already held or cooling down: reject and log.

## Tests

Implementation should add tests for:

- Candidate normalization defaults ambiguous entries to `watch_only`.
- Breakout does not buy before confirmation time.
- Breakout buys probe size only after confirmation.
- Pullback buys only inside pullback zone and above support.
- Watch-only never auto-buys.
- Daily new position cap rejects excess candidates.
- Strategy type is written to ledger entries.
- Trailing take-profit activates after configured gain.
- Mechanical stop does not call emergency Claude repeatedly.

## Rollout

Phase A: schema and deterministic routing.

- Add `strategy_type` normalization.
- Add watch-only and daily cap.
- Add breakout confirm-after rule.

Phase B: elasticity.

- Add probe sizing.
- Add pullback zone execution.
- Add simple trailing take-profit.

Phase C: comparison.

- Add report grouping by strategy type.
- Compare baseline vs elastic behavior over at least 20-30 paper/backtest trades before raising size.
