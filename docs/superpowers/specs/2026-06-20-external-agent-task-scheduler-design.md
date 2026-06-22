# External Agent Task Scheduler Design

## Goal

AlphaClaude should run scheduled Agent work as independent task processes. The app scheduler should create new Agent task sessions at fixed market times, similar to OpenClaw-style scheduled conversations, without running long Claude/Codex work inside the Feishu app scheduler thread.

The first version supports two built-in tasks:

- `premarket_plan`: scheduled before market open to produce a plan draft through the autonomous Agent workflow.
- `postclose_review`: scheduled after market close to produce an Agent-led review, attribution, and improvement report.

This design does not add arbitrary user-defined Agent cron tasks yet. Custom Agent scheduling needs separate permission, concurrency, and prompt-safety rules.

## Current Context

The existing app scheduler in `src/alphaclaude/app/scheduler.py` already uses APScheduler for memory tasks and paper engine start/stop tasks. It also still contains legacy direct LLM market report jobs.

The engine now has an autonomous Agent path:

- `AgentTaskRunner` creates the task prompt and artifact directory.
- The Agent must read `CLAUDE.md`, `skills/README.md`, and relevant `skills/*/SKILL.md` files by itself.
- The Agent must write dynamic subtask audit events with `python -m alphaclaude.engine.agent_event`.
- Dashboard can display `agent_runs/<task_id>/events.jsonl` through the existing timeline API.

The missing piece is a first-class scheduled Agent task entrypoint that can be launched as a separate process.

## Non-Goals

- Do not reintroduce hardcoded Python sub-agent orchestration.
- Do not inject skill contents into prompts by default.
- Do not run long Agent calls synchronously inside the scheduler job.
- Do not add live trading automation beyond the existing safety model.
- Do not build a general end-user cron editor for Agent tasks in this phase.
- Do not require the paper engine loop to be running before an Agent task can start.

## Architecture

### Process Boundary

Add an external process entrypoint to the engine CLI:

```powershell
python -m alphaclaude.engine.cli --agent-task premarket_plan --mode paper
python -m alphaclaude.engine.cli --agent-task postclose_review --mode paper
```

The scheduler only launches this command as a child process and records launch status. It does not wait for the Agent task to finish, parse the final report, or call Claude directly.

The child process owns:

- run directory creation
- task metadata
- Agent runner invocation
- workflow event recording
- timeout handling
- audit validation
- final status persistence

### Run Directory Layout

Each scheduled Agent task gets a run directory under `data/output/`:

```text
data/output/agent_YYYY-MM-DD_<task_id>/
  state.json
  workflow_events.jsonl
  agent_runs/
    <task_id>/
      prompt.md
      stdout.md
      stderr.md
      metadata.json
      events.jsonl
      tasks/
        <dynamic_subtask_id>/
          input.md
          output.md
          result.json
```

Task-specific top-level artifacts:

```text
agent_runs/premarket_plan/
  research_report.md
  candidate_evidence.json
  plan_draft.json

agent_runs/postclose_review/
  review_report.md
  strategy_attribution.json
```

### Scheduled Jobs

The app scheduler registers two built-in Agent jobs:

- `agent_premarket_plan`: trading days around 08:30.
- `agent_postclose_review`: trading days around 15:10 or 15:30.

Each job performs:

1. Check the trading calendar.
2. Check a per-day sentinel for the task.
3. Check whether the same task is already running.
4. Launch the CLI process.
5. Record launch metadata and return quickly.

The sentinel should be task-specific, for example:

```text
data/state/.agent_task_premarket_plan_last_start
data/state/.agent_task_postclose_review_last_start
```

The value is the latest trading date that was launched for that task.

### Agent Task Entrypoint

The CLI should dispatch by task id:

- `premarket_plan` uses `AgentTaskRunner.run_premarket_plan()`.
- `postclose_review` uses a new Agent task prompt that asks for a post-close review and writes review artifacts.

The post-close prompt should tell the Agent to inspect:

- `CLAUDE.md`
- `skills/README.md`
- relevant `skills/*/SKILL.md`
- current run `plan.json`
- `state.json`
- `ledger.jsonl`
- `workflow_events.jsonl`
- available daily reports

The Agent should produce:

- `review_report.md`: natural-language post-close review.
- `strategy_attribution.json`: structured attribution by trade, signal source, rule, outcome, and improvement suggestion.
- dynamic `agent_event` subtasks for any internal analysis work it starts.

### Interaction With Paper Engine

The scheduler-launched `premarket_plan` is independent from the paper engine loop. It should be able to create a plan draft before the engine starts or while the engine is not running.

The execution layer remains Python-only:

- Agent may produce `plan_draft.json`.
- Python risk validation decides what becomes executable plan state.
- If no valid plan exists, paper/live execution stays in observation mode or uses the existing safe fallback.

The scheduler may still start the paper engine separately. In the first implementation, the paper engine start job and Agent task launch job can both exist, but the Agent task is not executed inside the scheduler thread.

### Dashboard Visibility

Dashboard should display scheduled Agent runs through existing workflow and Agent timeline surfaces. The minimum acceptable visibility:

- list the scheduled Agent run as a run in the run selector or workflow API
- show `premarket_plan` and `postclose_review` workflow nodes
- show dynamic subtask timeline from `events.jsonl`
- show audit warnings from `metadata.json`
- allow opening input/output/result artifacts

If run selector integration is too large for the first implementation, an API-level route for scheduled Agent runs is acceptable as an interim step, but the artifacts must still follow the same structure.

## Error Handling

### Launch Failure

If `subprocess.Popen` or process creation fails, scheduler records a workflow/system warning and logs:

- task id
- command
- error string
- attempted launch time

The sentinel should not be updated on launch failure.

### Duplicate Task

If the same task has already started for the trading date, scheduler skips launch and logs a skip reason.

If a same-task process is still running, scheduler skips launch and records a warning instead of starting a second copy.

### Agent Failure

The CLI writes `metadata.json` with:

```json
{
  "ok": false,
  "returncode": 1,
  "error": "..."
}
```

It also writes a workflow event with status `failed` or `warning`.

### Audit Warnings

If `events.jsonl` is missing, malformed, has unclosed running tasks, or references missing artifacts, the CLI records `audit_warnings` in metadata and workflow output.

Audit warnings should not crash the app scheduler. They should be visible in Dashboard.

### Timeout

Timeout is enforced inside the Agent runner/CLI process. The scheduler should not block until timeout. Timeout results in failed task metadata and stderr capture.

## Safety

- Scheduled Agent tasks do not execute trades.
- `postclose_review` never changes plan or state.
- `premarket_plan` may write a draft, but Python risk validation is the only path to executable plan state.
- Live mode remains locked behind existing live trading safeguards.
- Agent artifact refs must stay inside the task artifact directory.
- Scheduler jobs must not interpolate user-provided shell strings.

## Testing

### Scheduler Tests

- Registers `agent_premarket_plan` and `agent_postclose_review`.
- Skips non-trading days.
- Skips when today's sentinel exists.
- Launches an external process command instead of directly invoking Agent runner.
- Does not update sentinel when launch fails.

### CLI Tests

- `--agent-task premarket_plan --mode paper` creates the expected run/task directory.
- `--agent-task postclose_review --mode paper` creates the expected run/task directory.
- Unknown task id exits with a clear error.
- Agent runner failures write failed metadata and workflow events.

### Audit Tests

- Scheduled task metadata includes audit warnings.
- Missing or malformed `events.jsonl` does not crash the CLI.
- Safe artifact path checks reject traversal.

### Dashboard/API Tests

- Timeline API can load `premarket_plan` from a scheduled Agent run.
- Timeline API can load `postclose_review` from a scheduled Agent run.
- Artifact API opens input/output/result refs for both tasks.

### Verification Commands

Use focused tests while implementing, then run:

```powershell
python -m pytest -q
python -m ruff check .
npm run dashboard:test
npm run dashboard:build
```

If tool schema surfaces changed, also run the schema smoke check required by project rules.

## Open Decisions

1. Exact launch time for `postclose_review`: initial default should be 15:30 to leave room for final ledger/state flush.
2. Whether `premarket_plan` should replace or coexist with the current paper engine premarket plan generation on the first release.
3. Whether scheduled Agent runs should use run ids prefixed with `agent_` or attach to the nearest `paper_` run.

Recommended defaults:

- `postclose_review` at 15:30.
- Coexist first, then remove duplicate engine-triggered Agent plan only after scheduled task reliability is verified.
- Use `agent_YYYY-MM-DD_<task_id>` for independent scheduled Agent runs.

