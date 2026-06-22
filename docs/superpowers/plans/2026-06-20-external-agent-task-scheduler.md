# External Agent Task Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add scheduled Agent tasks that the app scheduler launches as independent engine CLI processes.

**Architecture:** Introduce a focused `scheduled_agent_task` engine module that creates `agent_YYYY-MM-DD_<task_id>` run directories, invokes `AgentTaskRunner`, records workflow events, and writes state metadata. Extend `engine.cli` with `--agent-task`, extend `app.scheduler` to launch those CLI tasks through detached child processes with per-day sentinels, and include agent runs in Dashboard run listing/timeline.

**Tech Stack:** Python standard library, APScheduler, existing `AgentTaskRunner`, existing `WorkflowEventStore`, pytest, current Dashboard API.

---

### Task 1: Scheduled Agent Task Runner Module

**Files:**
- Create: `src/alphaclaude/engine/scheduled_agent_task.py`
- Test: `tests/engine/test_scheduled_agent_task.py`

- [x] Create tests for run id generation, premarket task execution, postclose task execution, unknown task rejection, metadata, and workflow events.
- [x] Implement `scheduled_run_id(task_id, today=None)`, `run_scheduled_agent_task(task_id, mode="paper", today=None)`, and task prompt/data helpers.
- [x] Reuse `AgentTaskRunner._run()` for `postclose_review` so output layout and audit validation stay identical.
- [x] Write `state.json` with `engine_meta.mode = "agent"`, `agent_task_id`, `process_id`, `status`, and timestamps.

### Task 2: CLI Entrypoint

**Files:**
- Modify: `src/alphaclaude/engine/cli.py`
- Test: `tests/engine/test_agent_task_cli.py`

- [x] Add `--agent-task` choices for `premarket_plan` and `postclose_review`.
- [x] Allow `--mode paper` with `--agent-task`.
- [x] Dispatch before normal engine mode validation.
- [x] Return JSON metadata on success and non-zero exit for failed task results.

### Task 3: Scheduler External Process Launch

**Files:**
- Modify: `src/alphaclaude/app/scheduler.py`
- Test: `tests/test_scheduler_config.py`

- [x] Add `agent_premarket_plan` and `agent_postclose_review` APScheduler jobs.
- [x] Implement trading-day checks, per-task sentinel checks, running-process checks, and detached `subprocess.Popen`.
- [x] Update sentinel only after successful child process creation.
- [x] Keep scheduler jobs fast and non-blocking.

### Task 4: Run Registry And Dashboard Visibility

**Files:**
- Modify: `src/alphaclaude/engine/run_registry.py`
- Modify: `src/alphaclaude/app/dashboard.py`
- Test: `tests/engine/test_run_registry.py`
- Test: `tests/test_dashboard_cache.py`

- [x] Let run registry include `agent_` run directories whose `state.json` has `engine_meta.mode = "agent"`.
- [x] Mark agent run liveness based on stored process id and `status`.
- [x] Ensure `/api/runs` includes scheduled Agent runs.
- [x] Ensure timeline/artifact APIs accept `postclose_review` the same way they accept `premarket_plan`.

### Task 5: Verification

- [x] Run focused Python tests for scheduler, CLI, scheduled task runner, run registry, and dashboard timeline.
- [x] Run `python -m pytest -q`.
- [x] Run `python -m ruff check .`.
- [x] Run `npm run dashboard:test`.
- [x] Run `npm run dashboard:build`.
