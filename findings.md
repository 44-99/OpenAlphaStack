# Architecture Findings

## Runtime Pause

- Process discovery found `python main.py` as PID 8484, launched from the AlphaClaude workspace through bash wrappers.
- `anthropic_proxy.py`, Codex, Claude HUD, and helper shell processes were left running.
- PID 8484 was suspended with `NtSuspendProcess`; result code 0 means Windows accepted the suspend call.

## Initial Architecture Notes

- Documentation claims a unified engine where backtest, paper, and live share one code path through `tools/paper_engine.py`.
- The live bot entrypoint is still `main.py`, which owns Feishu message handling, FastAPI, session persistence, skill loading, command parsing, queueing, stock context injection, and scheduler startup.
- `scheduler.py` owns both built-in scheduled reports and dynamic user tasks, and calls Claude directly for scheduled analysis.
- Current docs list dependency direction as `main -> memory, claude, scheduler, feishu, config, stock`; early reading confirms `main.py` is the orchestration hub.

## Architecture Findings

1. `tools/paper_engine.py` is a 4595-line monolith. It owns universe generation, state persistence, plan schema/migration, ledger, file lock, event queue, trading clock, execution, backtest data feed, overnight LLM pipeline, fast lane, engine orchestration, and CLI. This makes review and targeted tests difficult, especially for trading invariants.
2. `live` mode is exposed in CLI choices, but `ExecutionEngine` only routes orders into `EngineState` and `Ledger`. There is no brokerage adapter, paper/live order boundary, idempotency layer, order reconciliation, or kill switch interface. Current `live` appears behaviorally equivalent to paper mode.
3. The "unified path" claim is only partially true. Backtest uses `BacktestDataFeed`; paper/live fetch quotes through `_fallback.get_quote` and `quote.get_market_overview`. Risk validation also switches data sources by mode. This is an adapter pattern in disguise, but it is embedded inside business logic rather than isolated behind interfaces.
4. State encapsulation is weak. `EngineState.load()` and `PlanManager.load()` return shallow copies while other code directly reads and mutates `state._data` and `plan._data`. That bypasses save semantics and makes thread/process safety hard to reason about.
5. File locking only protects Claude session usage inside one output directory. State JSON, plan JSON, and ledger JSONL have per-instance thread locks, but no cross-process lock; `resume_run_id` or duplicate engine starts can write the same files concurrently.
6. Risk validation treats calculation/data exceptions as non-fatal and lets candidates pass. For trading, data/risk failures should usually fail closed or be explicitly classified.
7. Operational controls are inconsistent. In-app `/stop` kills `paper_engine.py` processes via SIGTERM/taskkill. There is no "pause/resume" command, no graceful engine control plane, and `engine_status.py` infers liveness by process mode plus newest output directory rather than by PID/run ID heartbeat.
8. Test coverage does not cover the trading engine. Current tests only exercise `anthropic_proxy.py` conversion behavior. There are no focused tests for `EngineState`, `PlanManager`, `ExecutionEngine`, `FastLane`, backtest replay, risk fail-closed behavior, or status detection.

## Suggested Refactor Direction

- First split `paper_engine.py` along real boundaries: `state_store`, `plan_store`, `ledger`, `market_data` adapters, `execution` adapters, `risk_pipeline`, `fast_lane`, `backtest_runner`, and `engine_app`.
- Introduce explicit `MarketDataAdapter` and `BrokerAdapter` interfaces. Backtest, paper, and live should differ only by adapters, not by scattered `if mode == "backtest"` branches.
- Make `live` unavailable or guarded until a real broker adapter, reconciliation loop, and manual approval/kill-switch policy exist.
- Replace `_data` access with methods or dataclasses/pydantic models, and use cross-process file locks for run state writes.
- Add a run registry/heartbeat file with PID, mode, run_id, started_at, last_tick_at, and status. Status and stop/pause should target run_id/PID, not "latest mode".
- Add tests around hard invariants before broad refactoring: cash cannot go negative, T+1 lock cannot be bypassed, stop-loss direction cannot invert, risk data failure policy is deterministic, duplicate/resume does not corrupt ledger sequence.
