# Autonomous Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old default premarket orchestration with an autonomous Agent research task that starts a fresh Claude/Codex-style task session, lets the Agent read `skills/` itself, and saves auditable artifacts before Python risk validation.

**Architecture:** Keep Python as scheduler, risk gate, and execution layer. Add a small `AgentTaskRunner` that builds task prompts, runs an Agent CLI, writes `prompt.md`, `stdout.md`, `stderr.md`, `metadata.json`, and parses optional JSON artifacts. `OvernightPipeline.run_full()` uses this Agent task as the default premarket path, then imports `plan_draft.json` into `PlanManager` and runs Python risk validation.

**Tech Stack:** Python standard library, existing `alphaclaude.config`, existing `CLAUDE_CMD`, pytest.

---

### Task 1: Agent Task Runner

**Files:**
- Create: `src/alphaclaude/engine/agent_task_runner.py`
- Test: `tests/engine/test_agent_task_runner.py`

- [ ] **Step 1: Write failing tests**

Create tests for prompt construction, artifact directory layout, subprocess invocation, timeout handling, and JSON artifact parsing.

```python
from pathlib import Path

from alphaclaude.engine.agent_task_runner import AgentTaskRunner


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_runner_writes_prompt_and_metadata(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out = tmp_path / "run" / "premarket_plan" / "plan_draft.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"market_bias":"neutral","buy_candidates":[]}', encoding="utf-8")
        return FakeCompleted(stdout="ok")

    monkeypatch.setattr("alphaclaude.engine.agent_task_runner.subprocess.run", fake_run)
    runner = AgentTaskRunner(output_dir=tmp_path / "run", run_id="paper_test", timeout=5)

    result = runner.run_premarket_plan(
        market_snapshot="MARKET",
        account_summary="ACCOUNT",
    )

    assert result.ok is True
    assert result.task_id == "premarket_plan"
    assert result.artifacts_dir == tmp_path / "run" / "agent_runs" / "premarket_plan"
    assert "skills/README.md" in (result.artifacts_dir / "prompt.md").read_text(encoding="utf-8")
    assert (result.artifacts_dir / "stdout.md").read_text(encoding="utf-8") == "ok"
    assert result.parsed_artifacts["plan_draft"]["market_bias"] == "neutral"
    assert calls[0][0][:2] == ["claude", "-p"]
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/engine/test_agent_task_runner.py -q`

Expected: FAIL because `alphaclaude.engine.agent_task_runner` does not exist.

- [ ] **Step 3: Implement the runner**

Implement:

```python
@dataclass
class AgentTaskResult:
    task_id: str
    ok: bool
    returncode: int
    artifacts_dir: Path
    stdout: str
    stderr: str
    parsed_artifacts: dict[str, Any]
    error: str = ""


class AgentTaskRunner:
    def __init__(self, output_dir: str | Path, run_id: str, agent_cmd: str | None = None, timeout: int | None = None):
        ...

    def run_premarket_plan(self, market_snapshot: str = "", account_summary: str = "") -> AgentTaskResult:
        ...
```

The prompt must tell the Agent:
- Read `CLAUDE.md` first.
- Read `skills/README.md`.
- Choose and read relevant `SKILL.md` and references by itself.
- Start or simulate three sub-agent workstreams: market direction, candidate discovery, holdings review.
- Use project tools as needed.
- Write `research_report.md`, `candidate_evidence.json`, and `plan_draft.json` into the provided artifact directory.
- Do not execute trades or edit source code.

- [ ] **Step 4: Run the test and verify pass**

Run: `python -m pytest tests/engine/test_agent_task_runner.py -q`

Expected: PASS.

### Task 2: Default Pipeline Integration

**Files:**
- Modify: `src/alphaclaude/config.py`
- Modify: `src/alphaclaude/engine/pipeline.py`
- Test: `tests/engine/test_pipeline_agent_workflow.py`

- [ ] **Step 1: Add timeout config**

Add:

```python
AGENT_WORKFLOW_TIMEOUT = int(os.getenv("AGENT_WORKFLOW_TIMEOUT", "900"))
```

- [ ] **Step 2: Write integration tests**

Test that default behavior calls `AgentTaskRunner`, records a workflow artifact, imports `plan_draft.json`, and does not bypass `run_risk_validation`.

- [ ] **Step 3: Wire as default**

In `OvernightPipeline.run_full()`:
- fetch and record `market_snapshot`.
- call `AgentTaskRunner.run_premarket_plan()`.
- record workflow event `agent_research`.
- if `plan_draft.json` exists, merge only the fields needed for candidate validation into `self.plan._data`.
- always run existing Python `run_risk_validation()` after any imported draft.
- remove the legacy direct API sub-agent, merged-stage, and bull/bear candidate orchestration from the default implementation.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m pytest tests/engine/test_agent_task_runner.py tests/engine/test_pipeline_agent_workflow.py -q
```

Expected: PASS.

### Task 3: Verification

**Files:**
- No new implementation files.

- [ ] **Step 1: Run pipeline fallback tests**

Run:

```powershell
python -m pytest tests/engine/test_pipeline_safe_fallback.py tests/engine/test_workflow_events.py -q
```

Expected: PASS.

- [ ] **Step 2: Run dashboard build if workflow nodes changed**

Run:

```powershell
npm run dashboard:build
```

Expected: PASS.

### Self-Review

- The plan makes autonomous Agent research the default premarket path.
- The Agent is told to read `skills/` itself instead of receiving sliced skill text.
- Python remains the risk gate and execution layer.
- The first implementation is testable without launching a real external Agent because subprocess is mocked.
