# Paper Idle Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `paper` alive as a real idle engine on closed-market days and show idle runs as healthy waiting engines in `/状态`.

**Architecture:** Extend `PaperEngine.run_paper()` with a closed-market idle phase that keeps one daemon alive and updates engine metadata as it moves between observation and active phases. Adjust run-registry and status-formatting logic so alive observation runs are treated as active and duplicate `start`/`resume` calls return the existing run instead of starting another process.

**Tech Stack:** Python, existing AlphaClaude engine/control-plane modules, pytest

---

### Task 1: Engine idle lifecycle

**Files:**
- Modify: `src/alphaclaude/engine/paper.py`
- Test: `tests/engine/test_paper_schedule.py`

- [ ] Add failing tests for closed-market observation staying alive and transitioning metadata.
- [ ] Implement a long-lived idle observation loop in `PaperEngine.run_paper()` that keeps the process alive on closed-market days.
- [ ] Update engine metadata writes so observation is explicit while idle and switches back to active once trading becomes actionable.
- [ ] Run the focused paper schedule tests and confirm the new idle behavior passes.

### Task 2: Control-plane idempotency

**Files:**
- Modify: `src/alphaclaude/engine/run_registry.py`
- Modify: `src/alphaclaude/engine/cli.py`
- Test: `tests/test_package_entrypoints.py`

- [ ] Add failing tests for `resume` and `start --mode paper --daemon` returning an existing alive paper run.
- [ ] Implement a helper to locate an existing active paper run and reuse it.
- [ ] Make daemon start/resume return existing run metadata instead of creating a duplicate paper process.
- [ ] Run the entrypoint/control-plane tests and confirm idempotency passes.

### Task 3: Status rendering

**Files:**
- Modify: `src/alphaclaude/tools/engine_status.py`
- Test: `tests/engine/test_monitoring_ops.py`

- [ ] Add failing tests for alive observation runs rendering as `休市待机` and counting as active.
- [ ] Update phase labeling, summary counting, and detail text to show idle observation reasons.
- [ ] Keep dead processes with stale observation metadata rendered as stopped.
- [ ] Run the monitoring/status tests and confirm display behavior passes.

### Task 4: Final verification

**Files:**
- Modify: `docs/superpowers/plans/2026-05-25-paper-idle-status.md`

- [ ] Run the targeted pytest commands for schedule, monitoring, and package entrypoints.
- [ ] Run `python -m compileall -q src\\alphaclaude`.
- [ ] Record any deviations directly in the final response rather than broadening scope.
