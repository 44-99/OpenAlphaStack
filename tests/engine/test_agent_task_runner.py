import json
import subprocess

from alphaclaude.engine.agent_task_runner import AgentTaskRunner


class FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_runner_writes_prompt_metadata_and_loads_artifacts(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        artifact_dir = tmp_path / "run" / "agent_runs" / "premarket_plan"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        task_dir = artifact_dir / "tasks" / "market_intel"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "input.md").write_text("input", encoding="utf-8")
        (task_dir / "output.md").write_text("output", encoding="utf-8")
        (task_dir / "result.json").write_text('{"ok":true}', encoding="utf-8")
        (artifact_dir / "events.jsonl").write_text(
            "\n".join([
                json.dumps({
                    "event_id": "agent_evt_1",
                    "task_id": "market_intel",
                    "parent_task_id": "premarket_plan",
                    "role": "市场情报",
                    "status": "running",
                    "input_ref": "tasks/market_intel/input.md",
                }, ensure_ascii=False),
                json.dumps({
                    "event_id": "agent_evt_2",
                    "task_id": "market_intel",
                    "parent_task_id": "premarket_plan",
                    "role": "市场情报",
                    "status": "success",
                    "output_ref": "tasks/market_intel/output.md",
                    "result_ref": "tasks/market_intel/result.json",
                }, ensure_ascii=False),
            ]),
            encoding="utf-8",
        )
        (artifact_dir / "plan_draft.json").write_text(
            json.dumps({"market_bias": "neutral", "buy_candidates": []}),
            encoding="utf-8",
        )
        return FakeCompleted(stdout="agent ok", stderr="", returncode=0)

    monkeypatch.setattr("alphaclaude.engine.agent_task_runner.subprocess.run", fake_run)

    runner = AgentTaskRunner(output_dir=tmp_path / "run", run_id="paper_test", agent_cmd="claude", timeout=5)
    result = runner.run_premarket_plan(market_snapshot="MARKET", account_summary="ACCOUNT")

    assert result.ok is True
    assert result.task_id == "premarket_plan"
    assert result.artifacts_dir == tmp_path / "run" / "agent_runs" / "premarket_plan"
    assert result.stdout == "agent ok"
    assert result.stderr == ""
    assert result.audit_warnings == []
    assert len(result.agent_events) == 2
    assert result.parsed_artifacts["plan_draft"]["market_bias"] == "neutral"
    assert "CLAUDE.md" in (result.artifacts_dir / "prompt.md").read_text(encoding="utf-8")
    assert "skills/README.md" in (result.artifacts_dir / "prompt.md").read_text(encoding="utf-8")
    assert "agent_event start" in (result.artifacts_dir / "prompt.md").read_text(encoding="utf-8")
    assert "agent_event finish" in (result.artifacts_dir / "prompt.md").read_text(encoding="utf-8")
    assert "MARKET" in (result.artifacts_dir / "prompt.md").read_text(encoding="utf-8")
    assert "ACCOUNT" in (result.artifacts_dir / "prompt.md").read_text(encoding="utf-8")
    assert (result.artifacts_dir / "stdout.md").read_text(encoding="utf-8") == "agent ok"
    assert (result.artifacts_dir / "metadata.json").exists()
    metadata = json.loads((result.artifacts_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["audit_warnings"] == []
    assert metadata["agent_events"] == 2
    assert calls[0][0][:2] == ["claude", "-p"]
    assert calls[0][1]["timeout"] == 5


def test_runner_reports_timeout_and_preserves_prompt(tmp_path, monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=3)

    monkeypatch.setattr("alphaclaude.engine.agent_task_runner.subprocess.run", fake_run)

    runner = AgentTaskRunner(output_dir=tmp_path / "run", run_id="paper_test", timeout=3)
    result = runner.run_premarket_plan()

    assert result.ok is False
    assert result.returncode == -1
    assert "timeout" in result.error.lower()
    assert result.audit_warnings == ["events.jsonl missing"]
    assert (result.artifacts_dir / "prompt.md").exists()
    metadata = json.loads((result.artifacts_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["ok"] is False


def test_runner_allows_agent_command_override(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeCompleted(stdout="ok")

    monkeypatch.setattr("alphaclaude.engine.agent_task_runner.subprocess.run", fake_run)

    runner = AgentTaskRunner(output_dir=tmp_path / "run", run_id="paper_test", agent_cmd="codex", timeout=7)
    result = runner.run_premarket_plan()

    assert result.ok is True
    assert result.audit_warnings == ["events.jsonl missing"]
    assert calls[0][0][:2] == ["codex", "-p"]
