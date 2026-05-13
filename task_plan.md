# AlphaClaude Architecture Review Plan

Goal: pause active trading/backtest execution, then review the repository architecture for unreasonable coupling, runtime risk, and mismatch between documented design and implementation.

## Phases

1. [complete] Pause active runtime
   - Identified `python main.py` as the AlphaClaude main process.
   - Suspended PID 8484 with `NtSuspendProcess`.
2. [complete] Map architecture and runtime boundaries
   - Read README, architecture docs, main entrypoint, scheduler, and core engine modules.
3. [complete] Inspect trading engine and state model
   - Review backtest, paper/live engine, shadow account, risk, signal, status modules.
4. [complete] Inspect dependency and test shape
   - Check imports, shared state, file IO boundaries, and tests.
5. [complete] Summarize architecture findings
   - Produce prioritized issues with evidence and concrete remediation options.
6. [complete] Start modularization scaffold
   - Add `src/alphaclaude` package namespace.
   - Add packaging metadata and console-script compatibility entrypoints.
   - Add smoke tests for package path and legacy CLI dispatch.
7. [complete] Convert engine monitoring checks to pytest
   - Added pytest coverage for stop-loss, take-profit, expiry close, T+0 cycle, cooldown, and bucket exposure.
   - Replaced the long-running `tools/test_ops.py` verification path with isolated mocked pytest tests.
8. [complete] Extract engine foundation modules
   - Moved constants, state, plan, ledger, and clock primitives into `src/alphaclaude/engine/`.
   - Updated `tools/paper_engine.py` to import those primitives while preserving legacy symbol names.
   - Updated engine monitoring tests to import extracted primitives from the package.
9. [complete] Extract execution engine
   - Moved `ExecutionEngine` into `src/alphaclaude/engine/execution.py`.
   - Kept `tools/paper_engine.py` exposing the same legacy `ExecutionEngine` symbol via import.
   - Replaced direct notifier coupling with an optional injected trade notification callback.
   - Updated engine monitoring tests to import `ExecutionEngine` from the package.
10. [complete] Extract fast-lane support primitives
   - Move `T0Tracker`, `SessionLock`, and `EventQueue` into focused modules under `src/alphaclaude/engine/`.
   - Keep legacy imports in `tools/paper_engine.py` so old scripts continue working.
   - Add package-level tests for T+0 tracker state and event queue persistence before moving more logic.
11. [complete] Extract data-feed adapter
   - Move `BacktestDataFeed` and day-bar generation into `src/alphaclaude/engine/data_feed.py`.
   - Keep `akshare` import isolation and `tools/signal.py` stdlib-shadowing workaround inside the adapter.
   - Preserve `python tools/paper_engine.py --mode backtest ...` behavior.
12. [complete] Extract `FastLane` after support primitives are stable
   - Move `FastLane` into `src/alphaclaude/engine/fast_lane.py`.
   - Keep signal scanning and data-feed calls injected or isolated so the package does not become tightly coupled to legacy `tools/` scripts.
   - Update monitoring tests to import `FastLane` from the package.
13. [complete] Extract orchestration last
   - Move `OvernightPipeline` and `PaperEngine` only after execution, data feed, and fast lane have package-level tests.
   - Package CLI usage is now proven, so migrated root/tool compatibility wrappers are removed in the next step.
14. [complete] Remove migrated compatibility entrypoints
   - Move engine CLI and universe selection fully into `src/alphaclaude/engine/`.
   - Move root app entrypoint into `src/alphaclaude/app/main.py`.
   - Delete migrated legacy files instead of keeping compatibility wrappers.
15. [complete] Move Python tools into package
   - Move remaining `tools/*.py` modules into `src/alphaclaude/tools/`.
   - Rewrite engine/app imports to package paths.
   - Remove old `tools/` directory after migration.
16. [complete] Sync docs with package refactor
   - Update README, active docs, and CLAUDE.md from root-script/legacy-tools commands to package entrypoints.
   - Mark `live` as a reserved entrypoint pending Phase 3 broker and safety-gate work.
   - Update historical comparison/spec docs where stale AlphaClaude paths could mislead future searches.
17. [complete] Prune README and refresh roadmap
   - Reduce README to project purpose, current status, install/run commands, common commands, and doc links.
   - Keep architecture diagrams in `docs/architecture.md` rather than README or roadmap.
   - Rewrite roadmap as a status/priority tracker based on current implementation and `docs/project-comparison.md` lessons.
18. [complete] Close Phase 2 structured-output reliability gap
   - Wire `call_with_tool_safe()` into OvernightPipeline direction, candidate, adjustment, and emergency Tool Use paths.
   - Add conservative fallback parsers so unstructured failures do not invent unsafe trade instructions.
   - Add pytest coverage for safe LLM fallback behavior and pipeline fallback paths.
   - Update roadmap status for API reliability and Shadow Account Phase B.
19. [complete] Align paper/backtest schedule with pre-market planning
   - Treat Claude Code plan generation as a pre-market-only step.
   - Keep intraday execution Python-only against `plan.json`.
   - Make post-close processing Python-only reporting, not plan generation.
   - Add observation-mode metadata when paper/live starts outside pre-market without an actionable plan.
20. [complete] Add non-blocking engine operations
   - Add `--daemon` to start paper/backtest/live engines as detached background processes with redirected log files.
   - Add `--stop-running` to stop recorded paper/backtest/live processes by PID metadata.
   - Cover daemon startup and PID-based stopping with package-entrypoint tests.
   - Validate by stopping the previous paper run and starting a fresh daemon paper run.

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| Access denied reading process command lines | `Get-CimInstance Win32_Process` in sandbox | Re-ran with approved escalation for process discovery |
| Null process handle from `Get-Process.Handle` | Direct `NtSuspendProcess($p.Handle)` | Switched to `OpenProcess(PROCESS_SUSPEND_RESUME)` |
| Access denied opening process for suspend | Non-elevated `OpenProcess` | Re-ran approved escalation and suspended PID 8484 |
| PowerShell range passed as a string | `Select-Object -Index 970..1105` | Retry with parenthesized range `(970..1105)` |
| Combined PowerShell index ranges as array | `Select-Object -Index (17..60),(280..325)` | Read ranges in separate tool calls |
| Old `tools/test_ops.py` hung for nearly an hour | `runpy.run_path("tools/test_ops.py")` | Killed stuck PID 13560; replaced coverage with isolated pytest tests and stopped running the old script |
| pytest `tmp_path` used inaccessible Windows temp | `tmp_path` fixture | Use project-local `data/test_tmp` per-test directories and clean them after each test |
| `E:\tmp` was inaccessible despite sandbox note | direct mkdir under `E:\tmp` | Use workspace-local `data/test_tmp` instead |
| Long-running engine command made the tool session appear stuck | direct `python -m alphaclaude.engine.cli --mode paper ...` | Added `--daemon` for detached startup and `--stop-running` for PID-based cleanup |
