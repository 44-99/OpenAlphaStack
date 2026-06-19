# Agent Subtask Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auditable dynamic subtask timeline for autonomous Agent runs without hardcoding which sub-agents the Agent must create.

**Architecture:** Python provides an `agent_event` protocol and CLI for append-only task events under `agent_runs/<task>/`. The main Agent remains free to create any subtask names and roles, while Dashboard reads the resulting `events.jsonl` and task artifacts dynamically. `AgentTaskRunner` validates the audit trail after the Agent exits and reports warnings through metadata and pipeline output.

**Tech Stack:** Python standard library, pytest, FastAPI route helpers, React/TypeScript, Vitest.

---

### Task 1: Agent Event Protocol

**Files:**
- Create: `src/alphaclaude/engine/agent_event.py`
- Test: `tests/engine/test_agent_event.py`

- [ ] Add `record_agent_event()`, `read_agent_events()`, and `validate_agent_events()` for `events.jsonl`.
- [ ] Add CLI subcommands `start` and `finish` via `python -m alphaclaude.engine.agent_event`.
- [ ] Tests cover append-only writes, input/output/result refs, malformed JSON diagnostics, unclosed running tasks, and missing artifacts.

### Task 2: Runner Prompt And Validation

**Files:**
- Modify: `src/alphaclaude/engine/agent_task_runner.py`
- Modify: `tests/engine/test_agent_task_runner.py`
- Modify: `src/alphaclaude/engine/pipeline.py`
- Modify: `tests/engine/test_pipeline_agent_workflow.py`

- [ ] Prompt requires Agent to use `agent_event` for every subtask it starts.
- [ ] Runner parses `events.jsonl`, validates subtask audit artifacts, and writes `audit_warnings` into `metadata.json`.
- [ ] Pipeline exposes `audit_warnings` in the `agent_research` workflow output.

### Task 3: Dashboard API

**Files:**
- Modify: `src/alphaclaude/app/dashboard.py`
- Modify: `tests/test_dashboard_cache.py`

- [ ] Add `/api/workflow/runs/{run_id}/agent-runs/{task_id}/timeline`.
- [ ] Return `events`, `warnings`, and `tasks` with safe artifact refs.
- [ ] Reject path traversal and support demo data.

### Task 4: Dashboard Timeline Panel

**Files:**
- Modify: `dashboard/src/types.ts`
- Modify: `dashboard/src/api.ts`
- Modify: `dashboard/src/components/WorkflowBoard.tsx`
- Modify: `dashboard/src/components/WorkflowBoard.test.ts`

- [ ] Fetch timeline when `agent_research` is selected.
- [ ] Render dynamic subtask status, role, summary, input/output/result refs, and validation warnings.
- [ ] Tests cover API helper types and timeline rendering helpers.

### Task 5: Verification

- [ ] Run `python -m pytest tests/engine/test_agent_event.py tests/engine/test_agent_task_runner.py tests/engine/test_pipeline_agent_workflow.py tests/test_dashboard_cache.py -q`.
- [ ] Run `npm run dashboard:test`.
- [ ] Run `npm run dashboard:build`.
