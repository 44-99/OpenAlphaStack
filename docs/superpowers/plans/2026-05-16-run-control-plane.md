# Run Control Plane Implementation Plan

Status: completed and folded into the unified AlphaClaude CLI.

## Current Public Commands

All user-facing run control goes through `alphaclaude`:

```bash
alphaclaude engine start --mode paper --daemon
alphaclaude engine list
alphaclaude engine status <run_id>
alphaclaude engine stop <run_id>
alphaclaude engine resume <run_id> --daemon
alphaclaude engine stop-running
```

The old standalone engine console entrypoint is removed. Internal adapter details may still exist inside package code, but they are not public CLI or documentation surface.

## Delivered Scope

- Added `alphaclaude.engine.run_registry` for run discovery, exact lookup, single-run stop, and safe resume planning.
- Extended engine metadata in `data/output/<run_id>/state.json`.
- Added unified CLI routing under `alphaclaude engine ...`.
- Added Feishu commands for `/status <run_id>`, `/stop <run_id>`, and `/resume <run_id>`.
- Kept `live` resume conservative; it cannot imply real order execution before Phase 3 safety gates.
- Replaced long-running legacy script checks with pytest coverage.

## Verification Baseline

```powershell
python -m pytest -q
python -m compileall -q src\alphaclaude
alphaclaude --help
alphaclaude engine start --help
alphaclaude tools quote --help
git diff --check
```

## Remaining Follow-Up

- Add richer offline tests for Shadow Account diagnostics and prompt injection.
- Add configurable two-model routing for cheap research plus stricter final decisions.
- Compress high-volume tool outputs before sending them into Claude Code context.
