# Run Control Plane Design

## Context

AlphaClaude now runs from the `src/alphaclaude/` package and starts long-running engines through the unified `alphaclaude engine ...` CLI. The current stop command can stop recorded paper/backtest/live processes, but it is still coarse grained: callers cannot reliably target one run by `run_id`, inspect a single run, or resume a run through a clear safety path.

This design adds a small run control plane around existing run directories. It does not introduce a database or a web UI. `data/output/<run_id>/state.json` remains the source of truth for run metadata.

## Goals

- Query paper, backtest, and live runs by `run_id`.
- Stop exactly one run by `run_id` without affecting other active runs.
- Resume a paper/backtest/live run by `run_id` through daemon startup.
- Make live resume conservative by default so it never resumes real trading without explicit safety gates.
- Keep all new controls scriptable from PowerShell, Feishu commands, or future service wrappers.

## Non-Goals

- No web dashboard in this phase.
- No database or durable scheduler.
- No full backtest checkpointing unless current state already supports it.
- No live trading admission. `live` remains reserved until BrokerAdapter, order idempotency, manual confirmation, and safety gates are implemented.

## CLI Surface

The control plane is exposed through the unified `alphaclaude engine ...` CLI.

```bash
alphaclaude engine list
alphaclaude engine status <run_id>
alphaclaude engine stop <run_id>
alphaclaude engine resume <run_id> --daemon
```

Optional filters may be added without changing the core contract:

```bash
alphaclaude engine list --mode paper
```

Existing `alphaclaude engine stop-running` remains as a bulk cleanup command, but ordinary operations should prefer `alphaclaude engine stop <run_id>`.

## Run Metadata

Each engine run records metadata in `state.json` under `engine_meta`.

```json
{
  "engine_meta": {
    "run_id": "paper_2026-05-13T18-54-54",
    "mode": "paper",
    "process_id": 18276,
    "status": "running",
    "started_at": "2026-05-13T18:54:57",
    "stopped_at": "",
    "resume_count": 0,
    "observation_mode": true,
    "observation_reason": "started during post_market without a pre-market plan"
  }
}
```

`status` is derived from PID liveness when possible. Stored status is a hint for historical inspection, not the only authority.

Allowed status values:

- `running`: recorded PID is alive.
- `stopped`: run has no live process.
- `paused`: engine is intentionally not executing trades.
- `observation`: engine is alive but should not execute trades.
- `unknown`: metadata exists but liveness cannot be determined.

## Components

### Run Registry

A small registry module scans `data/output/` for run directories named `paper_*`, `backtest_*`, or `live_*`. It reads `state.json`, extracts `engine_meta`, checks PID liveness through the existing `engine_status` helper, and returns normalized `RunRecord` objects.

The registry owns:

- `list_runs(mode: str | None) -> list[RunRecord]`
- `get_run(run_id: str) -> RunRecord`
- `stop_run(run_id: str) -> StopResult`
- `build_resume_args(run_id: str) -> ResumePlan`

### CLI Adapter

`alphaclaude.app.cli` is the user-facing command router. It delegates engine actions to the package engine adapter, which keeps process scanning and metadata normalization out of argument routing.

### Engine Metadata Writer

`PaperEngine` records `run_id`, `mode`, `process_id`, `started_at`, and status transitions. The same metadata path is used for paper, backtest, and reserved live mode.

## Data Flow

### List

1. CLI receives `alphaclaude engine list`.
2. Registry scans `data/output/`.
3. Registry reads each `state.json`.
4. Registry checks PID liveness.
5. CLI prints a JSON list by default or a compact table if a human format is kept.

### Status

1. CLI receives `alphaclaude engine status <run_id>`.
2. Registry resolves the exact run directory.
3. Registry returns one normalized record.
4. Missing run returns exit code `2` with a clear JSON error.

### Stop

1. CLI receives `alphaclaude engine stop <run_id>`.
2. Registry resolves the exact run and reads `process_id`.
3. If PID is alive, CLI signals only that PID.
4. Metadata is updated to `stopped` with `stopped_at`.
5. If PID is already gone, the command succeeds with `already_stopped: true`.

### Resume

1. CLI receives `alphaclaude engine resume <run_id> --daemon`.
2. Registry resolves mode and existing run directory.
3. CLI starts a detached engine process for `alphaclaude engine resume <run_id> --daemon`.
4. Resume metadata increments `resume_count`.
5. For live mode, the resumed engine must start in `paused` or `observation` status unless later Phase 3 safety gates explicitly authorize order execution.

## Live Mode Safety

`live` run control is supported for querying, stopping, and safe resume. It is not a live trading admission feature.

Live resume rules:

- Default resume status is `paused` or `observation`.
- No order execution may occur only because `alphaclaude engine resume <live_run_id>` was called.
- Future live activation must require a separate safety gate that checks `.env`, runtime confirmation, BrokerAdapter readiness, and manual approval.
- Tests must prove that live resume builds a safe daemon command and records the conservative status.

## Error Handling

- Unknown `run_id`: exit code `2`, JSON error with `run_id` and message.
- Missing or invalid `state.json`: list commands include the run with `status: "unknown"`; status commands return a readable error.
- Missing PID: stop returns `already_stopped: true`.
- PID belongs to a non-engine process: first version trusts recorded metadata only if the run directory mode is valid and PID is alive. A later hardening pass can compare command line when permissions allow.
- Resume without `--daemon`: reject by default for paper/live to avoid foreground hangs. Backtest may allow foreground only if explicitly requested later.

## Testing

Add tests that use workspace-local temporary run directories.

- `list_runs` includes paper/backtest/live records and ignores unrelated directories.
- `status-run` returns one normalized record.
- `stop-run` signals only the PID for the requested `run_id`.
- `stop-run` is idempotent when PID is absent or dead.
- `resume-run --daemon` builds a detached command with `--resume <run_id>` and without passing `--daemon` to the child.
- `live` resume produces a conservative paused/observation state.
- CLI help exposes the new controls.

Verification baseline:

```powershell
python -m pytest -q
python -m compileall -q src\alphaclaude
alphaclaude --help
alphaclaude engine start --help
alphaclaude tools quote --help
```

## Rollout

1. Add the registry module and tests.
2. Wire CLI flags to the registry.
3. Extend metadata writing where needed.
4. Keep `alphaclaude engine stop-running` as bulk cleanup.
5. Restart paper mode with `--daemon` after implementation to load the new code.

## Acceptance Criteria

- A paper, backtest, or live run can be queried by exact `run_id`.
- Stopping a run by `run_id` does not stop other active runs.
- Resuming a run by `run_id` starts a detached process and returns immediately.
- Live resume is conservative and cannot execute real orders by itself.
- Existing tests and CLI help checks pass.
