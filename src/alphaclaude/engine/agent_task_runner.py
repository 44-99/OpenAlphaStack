"""Autonomous Agent task runner for auditable research workflows."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from alphaclaude.config import CLAUDE_CMD, CLAUDE_TIMEOUT
from alphaclaude.engine.agent_event import read_agent_events, validate_agent_events
from alphaclaude.paths import PROJECT_ROOT


@dataclass(frozen=True)
class AgentTaskResult:
    task_id: str
    ok: bool
    returncode: int
    artifacts_dir: Path
    stdout: str
    stderr: str
    parsed_artifacts: dict[str, Any]
    audit_warnings: list[str]
    agent_events: list[dict[str, Any]]
    error: str = ""


class AgentTaskRunner:
    """Run a fresh external Agent task and persist its research artifacts.

    The runner intentionally does not execute trades or mutate plan/state.
    Python callers must inspect the output artifacts and apply their own gates.
    """

    def __init__(
        self,
        output_dir: str | Path,
        run_id: str,
        agent_cmd: str | None = None,
        timeout: int | None = None,
    ):
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.agent_cmd = agent_cmd or CLAUDE_CMD
        self.timeout = timeout or CLAUDE_TIMEOUT

    def run_premarket_plan(
        self,
        market_snapshot: str = "",
        account_summary: str = "",
    ) -> AgentTaskResult:
        task_id = "premarket_plan"
        artifacts_dir = self.output_dir / "agent_runs" / task_id
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        prompt = self._build_premarket_prompt(
            artifacts_dir=artifacts_dir,
            market_snapshot=market_snapshot,
            account_summary=account_summary,
        )
        return self._run(task_id, artifacts_dir, prompt)

    def _run(self, task_id: str, artifacts_dir: Path, prompt: str) -> AgentTaskResult:
        (artifacts_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        stdout = ""
        stderr = ""
        returncode = -1
        error = ""

        try:
            completed = subprocess.run(
                [self.agent_cmd, "-p", prompt],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=str(PROJECT_ROOT),
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            returncode = int(completed.returncode)
        except subprocess.TimeoutExpired as exc:
            error = f"timeout after {exc.timeout}s"
        except FileNotFoundError:
            error = f"agent command not found: {self.agent_cmd}"
        except (OSError, ValueError) as exc:
            error = str(exc)

        (artifacts_dir / "stdout.md").write_text(stdout, encoding="utf-8")
        (artifacts_dir / "stderr.md").write_text(stderr, encoding="utf-8")
        parsed = self._load_json_artifacts(artifacts_dir)
        audit = validate_agent_events(artifacts_dir)
        audit_warnings = list(audit.get("warnings") or [])
        agent_events = read_agent_events(artifacts_dir)
        ok = returncode == 0 and not error
        result = AgentTaskResult(
            task_id=task_id,
            ok=ok,
            returncode=returncode,
            artifacts_dir=artifacts_dir,
            stdout=stdout,
            stderr=stderr,
            parsed_artifacts=parsed,
            audit_warnings=audit_warnings,
            agent_events=agent_events,
            error=error,
        )
        self._write_metadata(result)
        return result

    def _build_premarket_prompt(
        self,
        artifacts_dir: Path,
        market_snapshot: str,
        account_summary: str,
    ) -> str:
        return f"""你是 AlphaClaude 盘前主研究 Agent。

任务目标：
1. 先阅读 `CLAUDE.md`，遵守项目准则。
2. 阅读 `skills/README.md`，再按任务需要自行选择并阅读 `skills/` 下的 `SKILL.md` 和 references。
3. 自主启动或模拟 3 个子 Agent 工作流：
   - A: 市场方向与情绪周期
   - B: 候选发现与板块轮动
   - C: 持仓复盘与错误模式
4. 子 Agent 应自行决定需要调用哪些 `python -m alphaclaude.tools.*` 工具。
5. 主 Agent 汇总子 Agent 结果，给出盘前研究结论和候选计划草案。

硬约束：
- 不要执行真实或模拟交易。
- 不要修改源代码。
- 不要写入 `plan.json`、`state.json` 或 `ledger.jsonl`。
- 只允许把研究产物写入：`{artifacts_dir}`
- 必须把无法获取的数据标记为缺失，不要编造。
- 候选必须包含证据，不能只写题材叙事。

审计协议（必须遵守）：
- 如果你启动任何子任务或子 Agent，不管数量和名称如何，都必须用 `agent_event` CLI 留痕。
- 子任务开始前，先把输入写入 `{artifacts_dir}/tasks/<task_id>/input.md`，然后执行：
  `python -m alphaclaude.engine.agent_event start --run-dir "{artifacts_dir}" --task-id "<task_id>" --parent-task-id "premarket_plan" --role "<角色>" --summary "<开始摘要>" --input-ref "tasks/<task_id>/input.md"`
- 子任务完成后，把输出写入 `{artifacts_dir}/tasks/<task_id>/output.md`，结构化摘要写入 `{artifacts_dir}/tasks/<task_id>/result.json`，然后执行：
  `python -m alphaclaude.engine.agent_event finish --run-dir "{artifacts_dir}" --task-id "<task_id>" --parent-task-id "premarket_plan" --role "<角色>" --status "success" --summary "<完成摘要>" --output-ref "tasks/<task_id>/output.md" --result-ref "tasks/<task_id>/result.json"`
- 如果子任务失败，也必须执行 finish，并使用 `--status "error"` 与 `--error "<错误原因>"`。
- 你可以自由决定 task_id、role、子任务数量和执行顺序；Python 只审计事件，不预设你的子 Agent。

必须产出以下文件：
- `research_report.md`: 中文研究报告，包含市场、候选、持仓复盘、关键风险。
- `candidate_evidence.json`: 每只候选的结构化证据包。
- `plan_draft.json`: 计划草案，字段至少包含 `market_bias`、`bias_confidence`、`position_cap_pct`、`preferred_sectors`、`avoid_sectors`、`buy_candidates`、`holding_adjustments`。

Python 风控会在你完成后重新验证 `plan_draft.json`，未通过的候选会被拒绝。

运行信息：
- run_id: `{self.run_id}`
- artifacts_dir: `{artifacts_dir}`
- current_time: `{datetime.now().isoformat()}`

市场快照：
```text
{market_snapshot or "(未提供)"}
```

账户摘要：
```text
{account_summary or "(未提供)"}
```
"""

    def _load_json_artifacts(self, artifacts_dir: Path) -> dict[str, Any]:
        artifacts: dict[str, Any] = {}
        for name in ("candidate_evidence", "plan_draft"):
            path = artifacts_dir / f"{name}.json"
            if not path.exists():
                continue
            try:
                artifacts[name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                artifacts[name] = None
        return artifacts

    def _write_metadata(self, result: AgentTaskResult) -> None:
        payload = {
            "task_id": result.task_id,
            "run_id": self.run_id,
            "ok": result.ok,
            "returncode": result.returncode,
            "error": result.error,
            "agent_cmd": self.agent_cmd,
            "artifacts_dir": str(result.artifacts_dir),
            "parsed_artifacts": sorted(result.parsed_artifacts.keys()),
            "audit_warnings": result.audit_warnings,
            "agent_events": len(result.agent_events),
            "created_at": datetime.now().isoformat(),
        }
        (result.artifacts_dir / "metadata.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
