# Paper Idle And Status Design

## Context

`paper` mode can currently be resumed on a non-trading day, but the engine exits quickly after marking `observation_mode`. The run is then reported as `stopped`, even though the exit was expected because the market was closed. This creates two problems:

- operationally, a manually resumed paper run does not stay available as a real background engine across weekends or other closed-market periods;
- from the Feishu side, `/状态` reports `已停止`, which looks like a fault instead of an intentional idle state.

The app process and Feishu WebSocket can stay healthy at the same time, so the problem is the paper engine lifecycle and its status presentation, not the chat transport itself.

## Goals

- Make `paper` a real long-running idle engine on closed-market days.
- Keep the same `run_id` alive across idle periods instead of exiting immediately.
- Let the idle engine transition into normal paper execution when the next trading session becomes actionable.
- Make repeated `start` or `resume` calls idempotent when the same paper run is already idle or running.
- Change `/状态` so closed-market idle runs are shown as healthy waiting engines, not stopped failures.

## Non-Goals

- No new scheduler service outside the existing engine process.
- No redesign of backtest or live mode semantics in this change.
- No database or remote control plane.
- No attempt to correct historical PnL or ledger numbers in old runs.

## User-Visible Behavior

### Closed-Market Resume

If `paper` is started or resumed on a weekend, holiday, or other non-trading period:

- the engine process stays alive in the background;
- the run is marked as an idle observation state rather than a stopped state;
- `/状态` shows a waiting label such as `休市待机` instead of `已停止`;
- the status output includes the idle reason so the user can see why trading is not proceeding.

### Trading-Day Transition

The same idle process keeps checking market calendar and session state. When the market reaches an actionable trading day and phase, the engine transitions into its normal paper workflow without requiring a new manual resume.

### Idempotent Control

If the user tries to start or resume a paper run that is already alive, the control plane returns the existing run metadata instead of launching a second process.

## Design

## 1. Engine Lifecycle

`PaperEngine.run_paper()` gains an explicit closed-market idle loop instead of returning immediately when trading is not actionable.

The lifecycle becomes:

`starting -> idle_observation -> pre_market -> trading -> post_market -> idle_observation`

Key rules:

- `idle_observation` is a real live state, not a postmortem label.
- the process remains alive while idle and periodically re-evaluates trading conditions;
- no plan generation, intraday execution, or post-close reporting is triggered while the engine is in a closed-market idle window;
- explicit stop requests still terminate the process cleanly.

The idle loop sleeps between checks and updates state metadata on each phase change plus periodic heartbeats, with a cadence chosen to keep `/状态` current without causing noisy disk churn.

## 2. Engine Metadata

`state.json -> engine_meta` remains the source of truth for run lifecycle.

The engine writes explicit idle-state metadata while the process is alive:

```json
{
  "engine_meta": {
    "status": "observation",
    "observation_mode": true,
    "observation_reason": "周日休市; waiting for next trading session",
    "process_id": 12345,
    "started_at": "2026-05-24T10:00:00",
    "stopped_at": "",
    "resume_count": 4
  }
}
```

Rules:

- `status: observation` means the process is alive and intentionally not trading;
- `stopped_at` stays empty while the idle engine is alive;
- `observation_reason` is required when the engine is in idle observation;
- when the process later enters active paper work, metadata transitions back to `running` and may clear or replace the observation reason.

## 3. Control Plane Semantics

The run registry and engine CLI treat an alive `paper` run in observation mode as active.

### Resume

`alphaclaude engine resume <run_id> --daemon`:

- returns the existing run if its PID is alive, regardless of whether the run is in active trading or idle observation;
- only starts a new daemon when the recorded process is not alive.

### Start

`alphaclaude engine start --mode paper --daemon`:

- checks for an existing alive paper run before daemon launch;
- if one exists, returns that run rather than starting a duplicate instance.

This keeps user operations idempotent and avoids split-brain writes to the same run directory.

## 4. Status Presentation

`engine_status` distinguishes between:

- `运行中`
- `休市待机`
- `已停止`
- `已完成` for backtests

Rules for paper-mode display:

- if PID is alive and `observation_mode` is true, show `休市待机`;
- if PID is alive and `observation_mode` is false, keep the existing active labels;
- if PID is not alive and the last known state was observation, do not pretend the run is healthy; show `已停止`, because the process is actually gone;
- the top summary line counts idle observation runs as active engines, not as stopped engines.

The detail block also shows the observation reason, for example:

`待机原因: 周日休市; waiting for next trading session`

This makes `/状态` explain the engine state instead of forcing the user to infer it from timestamps.

## 5. Data Flow

### Resume On Closed Market

1. User resumes a paper run.
2. CLI checks registry for an existing live run.
3. If none exists, daemon starts.
4. Engine initializes and detects closed market.
5. Engine writes `status=observation`, `observation_mode=true`, and the idle reason.
6. Engine enters the idle loop and remains alive.
7. `/状态` reports the run as `休市待机`.

### Resume While Already Idle

1. User resumes the same paper run again.
2. Registry sees the PID is alive.
3. CLI returns existing run metadata.
4. No second process is started.

### Market Opens Later

1. Idle loop detects a tradable session boundary.
2. Engine updates metadata from observation to active running.
3. Normal paper workflow resumes on the same run.
4. `/状态` switches from `休市待机` to the appropriate active phase.

## Error Handling

- If the recorded PID is dead, the run is not treated as idle even if old metadata still says `observation`.
- If metadata is incomplete, liveness still takes priority over stale stored status.
- If the engine cannot determine the calendar state, it stays conservative: remain in observation mode and record a reason rather than forcing active execution.
- If duplicate start or resume requests race, the second caller receives the already-active run after the registry check, not a second daemon.

## Testing

Add focused tests for:

- paper resume on a closed market keeps the process alive and marks observation mode;
- status formatting shows `休市待机` for alive observation runs;
- summary counts observation runs as active;
- a dead PID with stale observation metadata is still reported as stopped;
- repeated resume on an alive observation run is idempotent;
- repeated start on an alive paper run is idempotent;
- idle observation transitions into active paper mode when mocked calendar state becomes tradable.

## Risks

- The current paper loop may assume it can return on non-trading days; converting that path into a long-lived idle loop may expose hidden shutdown or scheduling assumptions.
- Idempotent `start` semantics need to avoid accidentally blocking legitimate new runs if the product later supports multiple concurrent paper runs by design.
- Status labels need to stay consistent between registry JSON output, `/状态` text rendering, and any future CLI output.

## Rollout

Implement this as a narrow paper-mode change:

1. engine idle lifecycle and metadata updates;
2. registry and daemon idempotency rules;
3. status rendering changes;
4. targeted tests for lifecycle and display.

This keeps the change focused on the current operational problem without broad engine refactoring.
