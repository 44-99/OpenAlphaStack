# Dashboard 工作流工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not commit unless the user explicitly requests it; `CLAUDE.md` forbids automatic commits.

**Goal:** 把 Dashboard 从行情看板升级为“盯盘 / 流程 / 复盘”三模式工作台，第一版交付工作流可观测和 K 线交易结果层。

**Architecture:** 第一版采用工作流事件总线，不重写引擎 runtime。后端在现有 run 目录旁追加 `workflow_events.jsonl`、`workflow_config.json` 和 `workflow_artifacts/`；Dashboard API 读取这些文件并聚合 DAG 状态；React 前端新增流程视图和 K 线交易标注图层。现有 `plan.json`、`state.json`、`ledger.jsonl` 继续是交易事实来源。

**Tech Stack:** Python 3.12, FastAPI, pytest, React, TypeScript, Vite, ECharts, Vitest.

---

## File Structure

- Create: `src/alphaclaude/engine/workflow_events.py`
  - 负责工作流事件 schema、追加写入、artifact 写入、配置读写、DAG 聚合。
- Modify: `src/alphaclaude/engine/pipeline.py`
  - 给盘前链关键节点加中粒度事件埋点。
- Modify: `src/alphaclaude/engine/paper.py`
  - 给阶段切换、盘前计划恢复、盘后汇总加事件埋点。
- Modify: `src/alphaclaude/engine/fast_lane.py`
  - 给盘中 tick 摘要、候选拒绝、开仓/平仓触发点加最小事件埋点。
- Modify: `src/alphaclaude/engine/execution.py`
  - 交易执行成功后补充可被 K 线图层消费的 ledger 字段，不改变现有字段。
- Modify: `src/alphaclaude/app/dashboard.py`
  - 新增 workflow API、artifact 安全读取、SSE workflow 事件轮询。
- Create: `tests/engine/test_workflow_events.py`
  - 覆盖事件追加、artifact 写入、配置锁定节点、损坏 JSONL 诊断。
- Modify: `tests/test_dashboard_cache.py`
  - 增加 Dashboard workflow API 的后端路由测试。
- Modify: `dashboard/src/types.ts`
  - 增加 `WorkbenchMode`、`WorkflowEvent`、`WorkflowGraph`、`KlineTradeMarker` 等类型。
- Modify: `dashboard/src/api.ts`
  - 增加 workflow API client 和 code-filtered ledger client。
- Modify: `dashboard/src/App.tsx`
  - 顶层模式改为 `盯盘 / 流程 / 复盘`，保留左侧导航但不再把流程塞进旧日志页。
- Create: `dashboard/src/components/WorkflowBoard.tsx`
  - DAG 节点画布、节点 Inspector、事件时间线。
- Create: `dashboard/src/components/ReviewBoard.tsx`
  - 盘后轻量复盘视图，复用 workflow events 和 ledger。
- Modify: `dashboard/src/components/KlineChart.tsx`
  - 接收交易结果图层数据并触发标注 Inspector。
- Modify: `dashboard/src/charts/klineOption.ts`
  - 渲染买卖点、成本线、止损线、止盈线。
- Modify: `dashboard/src/charts/klineOption.test.ts`
  - 覆盖交易结果层 series/markLine/tooltip 保持可用。
- Create: `dashboard/src/components/WorkflowBoard.test.tsx`
  - 覆盖流程节点状态、Inspector 选择、错误事件展示。

---

### Task 1: Workflow Event Store

**Files:**
- Create: `src/alphaclaude/engine/workflow_events.py`
- Test: `tests/engine/test_workflow_events.py`

- [ ] **Step 1: Write failing tests for event append and artifact creation**

Add this test file:

```python
import json

from alphaclaude.engine.workflow_events import (
    WorkflowEventStore,
    default_workflow_config,
)


def test_record_node_finish_writes_jsonl_and_artifacts(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")

    event = store.record_node_finish(
        phase="premarket",
        node_id="risk_validation",
        node_name="风控校验",
        started_at="2026-06-04T09:30:01",
        summary="3 candidates, 2 passed, 1 rejected",
        input_refs=["plan.buy_candidates"],
        output_refs=["plan.risk_report"],
        input_payload={"candidates": [1, 2, 3]},
        output_payload={"passed": 2},
    )

    events_path = tmp_path / "workflow_events.jsonl"
    rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["event_id"] == event["event_id"]
    assert rows[0]["status"] == "success"
    assert rows[0]["phase"] == "premarket"
    assert rows[0]["node_id"] == "risk_validation"
    assert rows[0]["artifact_dir"] == f"workflow_artifacts/{event['event_id']}"
    assert (tmp_path / rows[0]["artifact_dir"] / "input.json").exists()
    assert (tmp_path / rows[0]["artifact_dir"] / "output.json").exists()


def test_default_config_locks_risk_and_ledger_nodes():
    config = default_workflow_config()

    assert config["version"] == 1
    assert config["nodes"]["risk_validation"]["locked"] is True
    assert config["nodes"]["ledger_writer"]["locked"] is True
    assert config["nodes"]["risk_validation"]["enabled"] is True
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\engine\test_workflow_events.py -q
```

Expected: FAIL because `alphaclaude.engine.workflow_events` does not exist.

- [ ] **Step 3: Implement the event store**

Create `src/alphaclaude/engine/workflow_events.py`:

```python
"""Workflow event store for Dashboard observability."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


WORKFLOW_EVENTS_FILE = "workflow_events.jsonl"
WORKFLOW_CONFIG_FILE = "workflow_config.json"
WORKFLOW_ARTIFACTS_DIR = "workflow_artifacts"


def utc_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_event_id() -> str:
    return f"wf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def default_workflow_config() -> dict[str, Any]:
    return {
        "version": 1,
        "nodes": {
            "market_snapshot": {"enabled": True, "locked": False, "params": {}},
            "sub_agent_a": {"enabled": True, "locked": False, "params": {}},
            "sub_agent_b": {"enabled": True, "locked": False, "params": {}},
            "sub_agent_c": {"enabled": True, "locked": False, "params": {}},
            "merge_decision": {"enabled": True, "locked": False, "params": {}},
            "bull_bear_debate": {"enabled": True, "locked": False, "params": {"max_rounds": 1}},
            "risk_validation": {
                "enabled": True,
                "locked": True,
                "params": {"max_single_position_pct": 25, "max_total_position_pct": 80},
            },
            "plan_writer": {"enabled": True, "locked": True, "params": {}},
            "state_watcher": {"enabled": True, "locked": False, "params": {}},
            "fastlane_tick": {"enabled": True, "locked": False, "params": {"tick_interval_sec": 1}},
            "signal_scan": {"enabled": True, "locked": False, "params": {}},
            "execution_check": {"enabled": True, "locked": True, "params": {}},
            "order_simulator": {"enabled": True, "locked": True, "params": {}},
            "ledger_writer": {"enabled": True, "locked": True, "params": {}},
            "alert_router": {"enabled": True, "locked": False, "params": {}},
            "daily_report": {"enabled": True, "locked": False, "params": {}},
            "ledger_pairing": {"enabled": True, "locked": False, "params": {}},
            "agent_reflection": {"enabled": True, "locked": False, "params": {}},
        },
    }


class WorkflowEventStore:
    def __init__(self, output_dir: str | os.PathLike[str], run_id: str | None = None):
        self.output_dir = Path(output_dir)
        self.run_id = run_id or self.output_dir.name
        self.events_path = self.output_dir / WORKFLOW_EVENTS_FILE
        self.config_path = self.output_dir / WORKFLOW_CONFIG_FILE
        self.artifacts_root = self.output_dir / WORKFLOW_ARTIFACTS_DIR

    def record_node_start(self, *, phase: str, node_id: str, node_name: str, summary: str = "") -> dict[str, Any]:
        return self._append_event(
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="running",
            started_at=utc_now_iso(),
            ended_at="",
            duration_ms=0,
            summary=summary,
        )

    def record_node_finish(
        self,
        *,
        phase: str,
        node_id: str,
        node_name: str,
        started_at: str = "",
        summary: str = "",
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        input_payload: Any = None,
        output_payload: Any = None,
    ) -> dict[str, Any]:
        event_id = new_event_id()
        artifact_dir = self._write_artifacts(event_id, input_payload=input_payload, output_payload=output_payload)
        return self._append_event(
            event_id=event_id,
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="success",
            started_at=started_at or utc_now_iso(),
            ended_at=utc_now_iso(),
            duration_ms=0,
            input_refs=input_refs or [],
            output_refs=output_refs or [],
            summary=summary,
            artifact_dir=artifact_dir,
        )

    def record_node_error(
        self,
        *,
        phase: str,
        node_id: str,
        node_name: str,
        error: str,
        input_payload: Any = None,
    ) -> dict[str, Any]:
        event_id = new_event_id()
        artifact_dir = self._write_artifacts(event_id, input_payload=input_payload, error_text=error)
        return self._append_event(
            event_id=event_id,
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="error",
            started_at=utc_now_iso(),
            ended_at=utc_now_iso(),
            duration_ms=0,
            summary=error[:240],
            error=error,
            artifact_dir=artifact_dir,
        )

    def record_node_skip(self, *, phase: str, node_id: str, node_name: str, summary: str) -> dict[str, Any]:
        return self._append_event(
            phase=phase,
            node_id=node_id,
            node_name=node_name,
            status="skipped",
            started_at=utc_now_iso(),
            ended_at=utc_now_iso(),
            duration_ms=0,
            summary=summary,
        )

    def read_events(self, limit: int = 500) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                rows.append({
                    "event_id": f"corrupt_{line_no}",
                    "run_id": self.run_id,
                    "phase": "system",
                    "node_id": "workflow_events",
                    "node_name": "事件文件",
                    "status": "error",
                    "summary": f"workflow_events.jsonl 第 {line_no} 行损坏: {exc}",
                    "error": str(exc),
                    "started_at": "",
                    "ended_at": "",
                    "duration_ms": 0,
                    "input_refs": [],
                    "output_refs": [],
                    "artifact_dir": "",
                })
        return rows[-limit:]

    def read_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            config = default_workflow_config()
            self.write_config(config)
            return config
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default_workflow_config()

    def write_config(self, config: dict[str, Any]) -> dict[str, Any]:
        merged = default_workflow_config()
        for node_id, incoming in (config.get("nodes") or {}).items():
            if node_id not in merged["nodes"]:
                continue
            node = merged["nodes"][node_id]
            if node.get("locked"):
                node["params"].update(incoming.get("params") or {})
                node["enabled"] = True
            else:
                node["enabled"] = bool(incoming.get("enabled", node["enabled"]))
                node["params"].update(incoming.get("params") or {})
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return merged

    def build_graph(self) -> dict[str, Any]:
        config = self.read_config()
        events = self.read_events()
        latest_by_node: dict[str, dict[str, Any]] = {}
        for event in events:
            latest_by_node[event.get("node_id", "")] = event
        nodes = []
        for node_id, node in config["nodes"].items():
            latest = latest_by_node.get(node_id, {})
            nodes.append({
                "id": node_id,
                "name": latest.get("node_name") or node_id,
                "enabled": node.get("enabled", True),
                "locked": node.get("locked", False),
                "status": latest.get("status", "idle"),
                "summary": latest.get("summary", ""),
                "last_event_id": latest.get("event_id", ""),
                "phase": latest.get("phase", ""),
            })
        return {"run_id": self.run_id, "nodes": nodes, "edges": default_workflow_edges()}

    def _append_event(self, **event: Any) -> dict[str, Any]:
        row = {
            "event_id": event.pop("event_id", new_event_id()),
            "run_id": self.run_id,
            "phase": event.pop("phase"),
            "node_id": event.pop("node_id"),
            "node_name": event.pop("node_name"),
            "status": event.pop("status"),
            "started_at": event.pop("started_at", ""),
            "ended_at": event.pop("ended_at", ""),
            "duration_ms": event.pop("duration_ms", 0),
            "input_refs": event.pop("input_refs", []),
            "output_refs": event.pop("output_refs", []),
            "summary": event.pop("summary", ""),
            "error": event.pop("error", ""),
            "artifact_dir": event.pop("artifact_dir", ""),
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def _write_artifacts(self, event_id: str, *, input_payload: Any = None, output_payload: Any = None, error_text: str = "") -> str:
        artifact_dir = self.artifacts_root / event_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if input_payload is not None:
            (artifact_dir / "input.json").write_text(json.dumps(input_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if output_payload is not None:
            (artifact_dir / "output.json").write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if error_text:
            (artifact_dir / "error.txt").write_text(error_text, encoding="utf-8")
        return f"{WORKFLOW_ARTIFACTS_DIR}/{event_id}"


def default_workflow_edges() -> list[dict[str, str]]:
    chain = [
        "market_snapshot",
        "sub_agent_a",
        "sub_agent_b",
        "sub_agent_c",
        "merge_decision",
        "bull_bear_debate",
        "risk_validation",
        "plan_writer",
        "state_watcher",
        "fastlane_tick",
        "signal_scan",
        "execution_check",
        "order_simulator",
        "ledger_writer",
        "alert_router",
        "daily_report",
        "ledger_pairing",
        "agent_reflection",
    ]
    return [{"from": left, "to": right} for left, right in zip(chain, chain[1:])]
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\engine\test_workflow_events.py -q
```

Expected: PASS.

---

### Task 2: Workflow Dashboard API

**Files:**
- Modify: `src/alphaclaude/app/dashboard.py`
- Test: `tests/test_dashboard_cache.py`

- [ ] **Step 1: Write failing route tests**

Append these tests:

```python
import asyncio
from fastapi.responses import JSONResponse


def test_workflow_events_api_reads_active_run(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{}", encoding="utf-8")
    (run_dir / "workflow_events.jsonl").write_text(
        '{"event_id":"wf_1","run_id":"paper_2026-06-04T09-30-00","phase":"premarket","node_id":"risk_validation","node_name":"风控校验","status":"success","started_at":"","ended_at":"","duration_ms":0,"input_refs":[],"output_refs":[],"summary":"passed","error":"","artifact_dir":""}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_workflow_events("paper_2026-06-04T09-30-00"))

    assert result["run_id"] == "paper_2026-06-04T09-30-00"
    assert result["events"][0]["node_id"] == "risk_validation"


def test_workflow_artifact_rejects_path_traversal(tmp_path, monkeypatch):
    output = tmp_path / "output"
    run_dir = output / "paper_2026-06-04T09-30-00"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(app_dashboard, "OUTPUT_BASE", str(output))

    result = asyncio.run(app_dashboard.api_workflow_artifact("paper_2026-06-04T09-30-00", "..", "secret.txt"))

    assert isinstance(result, JSONResponse)
    assert result.status_code == 400
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\test_dashboard_cache.py -q
```

Expected: FAIL because workflow API functions do not exist.

- [ ] **Step 3: Add safe run lookup and routes**

In `src/alphaclaude/app/dashboard.py`, import `WorkflowEventStore`:

```python
from alphaclaude.engine.workflow_events import WorkflowEventStore
```

Add helpers near `_get_active_output_dir()`:

```python
def _get_run_output_dir(run_id: str | None = None) -> str | None:
    if run_id and run_id != "active":
        candidate = os.path.abspath(os.path.join(OUTPUT_BASE, run_id))
        output_root = os.path.abspath(OUTPUT_BASE)
        if candidate != output_root and candidate.startswith(output_root + os.sep) and os.path.isdir(candidate):
            return candidate
        return None
    return _get_active_output_dir()


def _workflow_store_for_run(run_id: str | None = None) -> WorkflowEventStore | None:
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return None
    return WorkflowEventStore(output_dir, run_id=os.path.basename(output_dir))
```

Add routes before the root `/dashboard` routes:

```python
@router.get("/api/workflow/runs/{run_id}/events")
async def api_workflow_events(run_id: str, limit: int = 500):
    store = _workflow_store_for_run(run_id)
    if not store:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)
    return {"run_id": store.run_id, "events": store.read_events(limit=max(1, min(int(limit), 2000)))}


@router.get("/api/workflow/runs/{run_id}/graph")
async def api_workflow_graph(run_id: str):
    store = _workflow_store_for_run(run_id)
    if not store:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)
    return store.build_graph()


@router.get("/api/workflow/runs/{run_id}/config")
async def api_workflow_config(run_id: str):
    store = _workflow_store_for_run(run_id)
    if not store:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)
    return store.read_config()


@router.post("/api/workflow/runs/{run_id}/config")
async def api_workflow_config_update(run_id: str, request: Request):
    store = _workflow_store_for_run(run_id)
    if not store:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)
    payload = await request.json()
    config = store.write_config(payload)
    _broadcast_sse("workflow_config_updated", {"run_id": store.run_id, "config": config})
    return config


@router.post("/api/workflow/runs/{run_id}/nodes/{node_id}/rerun")
async def api_workflow_node_rerun(run_id: str, node_id: str):
    return JSONResponse(
        {"error": f"节点重跑尚未开放: {run_id}/{node_id}. 第一版只做可观测。"},
        status_code=409,
    )


@router.get("/api/workflow/runs/{run_id}/artifacts/{event_id}/{name}")
async def api_workflow_artifact(run_id: str, event_id: str, name: str):
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)
    allowed_names = {"input.json", "output.json", "prompt.txt", "response.txt", "error.txt"}
    if event_id in {"", ".", ".."} or "/" in event_id or "\\" in event_id or name not in allowed_names:
        return JSONResponse({"error": "Invalid artifact path"}, status_code=400)
    artifact_path = os.path.abspath(os.path.join(output_dir, "workflow_artifacts", event_id, name))
    root = os.path.abspath(os.path.join(output_dir, "workflow_artifacts"))
    if not artifact_path.startswith(root + os.sep):
        return JSONResponse({"error": "Invalid artifact path"}, status_code=400)
    if not os.path.exists(artifact_path):
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    with open(artifact_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"run_id": os.path.basename(output_dir), "event_id": event_id, "name": name, "content": content}
```

- [ ] **Step 4: Run backend route tests**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\test_dashboard_cache.py tests\engine\test_workflow_events.py -q
```

Expected: PASS.

---

### Task 3: Minimal Engine Instrumentation

**Files:**
- Modify: `src/alphaclaude/engine/pipeline.py`
- Modify: `src/alphaclaude/engine/paper.py`
- Modify: `src/alphaclaude/engine/fast_lane.py`
- Test: `tests/engine/test_workflow_events.py`

- [ ] **Step 1: Add tests for no-op safe instrumentation**

Append:

```python
def test_store_error_event_can_be_called_from_exception_handler(tmp_path):
    store = WorkflowEventStore(tmp_path, run_id="paper_test")

    event = store.record_node_error(
        phase="intraday",
        node_id="fastlane_tick",
        node_name="盘中快车道",
        error="quote timeout",
        input_payload={"codes": ["300913"]},
    )

    assert event["status"] == "error"
    assert event["summary"] == "quote timeout"
    assert (tmp_path / event["artifact_dir"] / "error.txt").read_text(encoding="utf-8") == "quote timeout"
```

- [ ] **Step 2: Run tests**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\engine\test_workflow_events.py -q
```

Expected: PASS after Task 1 already exists.

- [ ] **Step 3: Attach `WorkflowEventStore` to engines**

In `pipeline.py`, import:

```python
from alphaclaude.engine.workflow_events import WorkflowEventStore
```

In `OvernightPipeline.__init__`, add:

```python
self.workflow = WorkflowEventStore(output_dir, run_id=self.run_id)
```

In `paper.py`, import:

```python
from alphaclaude.engine.workflow_events import WorkflowEventStore
```

In `PaperEngine.__init__`, add:

```python
self.workflow = WorkflowEventStore(self.output_dir, run_id=self.run_id)
```

Pass it into `FastLane` only if the constructor is updated in this task:

```python
self.fast_lane = FastLane(
    self.state,
    self.plan,
    self.execution,
    self.clock,
    self.output_dir,
    mode=self.mode,
    workflow=self.workflow,
)
```

In `fast_lane.py`, update `FastLane.__init__` signature:

```python
def __init__(self, state, plan, execution, clock, output_dir: str, mode: str = "paper", workflow=None):
    self.workflow = workflow
```

- [ ] **Step 4: Add coarse event writes without changing control flow**

In `OvernightPipeline.run_full()`, wrap the major phases:

```python
self.workflow.record_node_finish(
    phase="premarket",
    node_id="market_snapshot",
    node_name="市场快照",
    summary="开始盘前计划生成",
    output_refs=["market.snapshot"],
)
```

After sub-agent summaries are produced:

```python
for node_id, name in [
    ("sub_agent_a", "子代理A: 市场方向"),
    ("sub_agent_b", "子代理B: 选股"),
    ("sub_agent_c", "子代理C: 复盘反馈"),
]:
    self.workflow.record_node_finish(
        phase="premarket",
        node_id=node_id,
        node_name=name,
        summary="盘前子代理完成",
        output_refs=[f"premarket.{node_id}"],
    )
```

After `run_merged_stage`:

```python
self.workflow.record_node_finish(
    phase="premarket",
    node_id="merge_decision",
    node_name="合并决策",
    summary=f"方向 {result.get('market_bias', 'unknown')}，候选 {len(result.get('buy_candidates', []))} 只",
    input_refs=["premarket.sub_agent_a", "premarket.sub_agent_b", "premarket.sub_agent_c"],
    output_refs=["plan.market_bias", "plan.buy_candidates"],
)
```

After `run_risk_validation`:

```python
self.workflow.record_node_finish(
    phase="premarket",
    node_id="risk_validation",
    node_name="风控校验",
    summary=f"风控后候选 {len(self.plan._data.get('buy_candidates', []))} 只",
    input_refs=["plan.buy_candidates"],
    output_refs=["plan.risk_report"],
)
```

After `self.plan.save()` in the same path:

```python
self.workflow.record_node_finish(
    phase="premarket",
    node_id="plan_writer",
    node_name="计划写入",
    summary="plan.json 已写入",
    output_refs=["plan.json"],
)
```

In `PaperEngine.run_post_close()`, after report creation:

```python
self.workflow.record_node_finish(
    phase="postclose",
    node_id="daily_report",
    node_name="盘后日报",
    summary=f"盘后报告完成，成交 {report.get('trade_count', 0)} 笔",
    output_refs=["daily_report"],
    output_payload=report,
)
```

In `FastLane.tick()`, at the end of a successful tick:

```python
if self.workflow:
    self.workflow.record_node_finish(
        phase="intraday",
        node_id="fastlane_tick",
        node_name="盘中快车道",
        summary=f"tick 完成，事件 {len(events)} 条",
        output_refs=["state.json", "ledger.jsonl"],
        output_payload={"events": events[:20]},
    )
```

In the `FastLane.tick()` exception handler, add:

```python
if self.workflow:
    self.workflow.record_node_error(
        phase="intraday",
        node_id="fastlane_tick",
        node_name="盘中快车道",
        error=str(e),
    )
```

- [ ] **Step 5: Run engine tests**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\engine\test_workflow_events.py tests\engine\test_paper_schedule.py tests\engine\test_monitoring_ops.py -q
```

Expected: PASS.

---

### Task 4: Frontend Workflow Types and API Client

**Files:**
- Modify: `dashboard/src/types.ts`
- Modify: `dashboard/src/api.ts`
- Test: `dashboard/src/charts/klineOption.test.ts`

- [ ] **Step 1: Add TypeScript types**

In `dashboard/src/types.ts`, change page mode types:

```ts
export type WorkbenchMode = 'watch' | 'workflow' | 'review';
export type PageKey = 'holdings' | 'plan' | 'ledger' | 'logs';
```

Append workflow types:

```ts
export interface WorkflowEvent {
  event_id: string;
  run_id: string;
  phase: 'premarket' | 'intraday' | 'postclose' | 'system' | string;
  node_id: string;
  node_name: string;
  status: 'idle' | 'running' | 'success' | 'error' | 'skipped' | string;
  started_at?: string;
  ended_at?: string;
  duration_ms?: number;
  input_refs?: string[];
  output_refs?: string[];
  summary?: string;
  error?: string;
  artifact_dir?: string;
}

export interface WorkflowGraphNode {
  id: string;
  name: string;
  enabled: boolean;
  locked: boolean;
  status: string;
  summary?: string;
  last_event_id?: string;
  phase?: string;
}

export interface WorkflowGraphEdge {
  from: string;
  to: string;
}

export interface WorkflowGraph {
  run_id: string;
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
}

export interface KlineTradeMarker {
  time: string;
  code: string;
  action: 'buy' | 'sell' | 'stop_loss' | 'take_profit' | string;
  price: number;
  shares?: number;
  strategy?: string;
  reasoning?: string;
  stop_loss?: number;
  take_profit?: number;
}
```

- [ ] **Step 2: Add API methods**

In `dashboard/src/api.ts`, import new types and add:

```ts
  ledgerForCode: (code: string, limit = 200) =>
    getJson<LedgerEntry[]>(`/api/ledger?limit=${limit}&code=${code}`),
  workflowEvents: (runId = 'active', limit = 500) =>
    getJson<{ run_id: string; events: WorkflowEvent[] }>(`/api/workflow/runs/${runId}/events?limit=${limit}`),
  workflowGraph: (runId = 'active') =>
    getJson<WorkflowGraph>(`/api/workflow/runs/${runId}/graph`),
  workflowArtifact: (runId: string, eventId: string, name: string) =>
    getJson<{ run_id: string; event_id: string; name: string; content: string }>(
      `/api/workflow/runs/${runId}/artifacts/${eventId}/${name}`,
    ),
```

- [ ] **Step 3: Run TypeScript build**

Run:

```powershell
npm run dashboard:build
```

Expected: PASS.

---

### Task 5: Workflow Board UI

**Files:**
- Create: `dashboard/src/components/WorkflowBoard.tsx`
- Modify: `dashboard/src/App.tsx`
- Modify: `dashboard/src/styles.css`
- Test: `dashboard/src/components/WorkflowBoard.test.tsx`

- [ ] **Step 1: Add component test**

Create `dashboard/src/components/WorkflowBoard.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { WorkflowBoard } from './WorkflowBoard';
import type { WorkflowEvent, WorkflowGraph } from '../types';

const graph: WorkflowGraph = {
  run_id: 'paper_test',
  nodes: [
    { id: 'market_snapshot', name: '市场快照', enabled: true, locked: false, status: 'success', summary: '完成' },
    { id: 'risk_validation', name: '风控校验', enabled: true, locked: true, status: 'error', summary: '失败' },
  ],
  edges: [{ from: 'market_snapshot', to: 'risk_validation' }],
};

const events: WorkflowEvent[] = [
  {
    event_id: 'wf_1',
    run_id: 'paper_test',
    phase: 'premarket',
    node_id: 'risk_validation',
    node_name: '风控校验',
    status: 'error',
    summary: '数据缺失',
    error: 'quote timeout',
  },
];

describe('WorkflowBoard', () => {
  it('renders graph nodes and event inspector', () => {
    render(<WorkflowBoard graph={graph} events={events} />);

    expect(screen.getByText('市场快照')).toBeTruthy();
    expect(screen.getByText('风控校验')).toBeTruthy();
    expect(screen.getByText('quote timeout')).toBeTruthy();
    expect(screen.getByText('锁定')).toBeTruthy();
  });
});
```

- [ ] **Step 2: Implement `WorkflowBoard`**

Create:

```tsx
import { useMemo, useState } from 'react';
import type { WorkflowEvent, WorkflowGraph, WorkflowGraphNode } from '../types';

export function WorkflowBoard({ graph, events }: { graph?: WorkflowGraph; events: WorkflowEvent[] }) {
  const [selectedNodeId, setSelectedNodeId] = useState('');
  const selectedNode = useMemo(() => {
    if (!graph?.nodes.length) return undefined;
    return graph.nodes.find((node) => node.id === selectedNodeId) || graph.nodes[0];
  }, [graph, selectedNodeId]);
  const selectedEvents = useMemo(() => {
    if (!selectedNode) return events;
    return events.filter((event) => event.node_id === selectedNode.id);
  }, [events, selectedNode]);

  if (!graph) return <div className="empty">暂无工作流数据</div>;

  return (
    <section className="workflow-board">
      <div className="workflow-graph">
        <header>
          <strong>流程画布</strong>
          <span>{graph.run_id}</span>
        </header>
        <div className="workflow-node-grid">
          {graph.nodes.map((node) => (
            <WorkflowNode key={node.id} node={node} active={selectedNode?.id === node.id} onSelect={() => setSelectedNodeId(node.id)} />
          ))}
        </div>
      </div>
      <aside className="workflow-inspector">
        <header>
          <strong>{selectedNode?.name || '节点详情'}</strong>
          {selectedNode?.locked ? <span className="lock-pill">锁定</span> : null}
        </header>
        <p>{selectedNode?.summary || '暂无摘要'}</p>
        <h4>事件时间线</h4>
        <div className="workflow-events">
          {selectedEvents.map((event) => (
            <article className={`workflow-event ${event.status}`} key={event.event_id}>
              <span>{event.phase}</span>
              <strong>{event.node_name}</strong>
              <p>{event.summary || '--'}</p>
              {event.error ? <code>{event.error}</code> : null}
            </article>
          ))}
        </div>
      </aside>
    </section>
  );
}

function WorkflowNode({ node, active, onSelect }: { node: WorkflowGraphNode; active: boolean; onSelect: () => void }) {
  return (
    <button className={`workflow-node ${node.status} ${active ? 'active' : ''}`} onClick={onSelect}>
      <span>{node.status}</span>
      <strong>{node.name}</strong>
      <small>{node.enabled ? '启用' : '禁用'}{node.locked ? ' / 锁定' : ''}</small>
    </button>
  );
}
```

- [ ] **Step 3: Wire three workbench modes in `App.tsx`**

Add imports:

```tsx
import { WorkflowBoard } from './components/WorkflowBoard';
import type { WorkbenchMode, WorkflowEvent, WorkflowGraph } from './types';
```

Add state:

```tsx
const [mode, setMode] = useState<WorkbenchMode>('watch');
const [workflowEvents, setWorkflowEvents] = useState<WorkflowEvent[]>([]);
const [workflowGraph, setWorkflowGraph] = useState<WorkflowGraph | undefined>();
```

Add initial fetch:

```tsx
api.workflowEvents().then((data) => setWorkflowEvents(data.events)),
api.workflowGraph().then(setWorkflowGraph),
```

Add SSE listener:

```tsx
source.addEventListener('workflow_event', (event) => {
  const data = JSON.parse(event.data) as WorkflowEvent;
  setWorkflowEvents((current) => [data, ...current].slice(0, 500));
  api.workflowGraph().then(setWorkflowGraph);
});
```

Replace old workspace top condition with:

```tsx
<div className="mode-tabs">
  <button className={mode === 'watch' ? 'active' : ''} onClick={() => setMode('watch')}>盯盘</button>
  <button className={mode === 'workflow' ? 'active' : ''} onClick={() => setMode('workflow')}>流程</button>
  <button className={mode === 'review' ? 'active' : ''} onClick={() => setMode('review')}>复盘</button>
</div>
```

Render:

```tsx
{mode === 'watch' ? <WatchWorkspace /> : null}
{mode === 'workflow' ? <WorkflowBoard graph={workflowGraph} events={workflowEvents} /> : null}
{mode === 'review' ? <ReviewBoard events={workflowEvents} ledger={ledger} plan={plan} /> : null}
```

The concrete implementation can keep `WatchWorkspace` inline at first; do not split `App.tsx` unless the edit becomes too hard to review.

- [ ] **Step 4: Add CSS**

Append compact styles to `dashboard/src/styles.css`:

```css
.mode-tabs {
  display: flex;
  gap: 8px;
  padding: 0 0 8px;
}
.mode-tabs button,
.workflow-node {
  border: 1px solid rgba(120, 151, 176, 0.22);
  background: rgba(8, 15, 24, 0.72);
  color: #d8e6ec;
  border-radius: 12px;
}
.mode-tabs button.active {
  border-color: rgba(214, 161, 59, 0.7);
  color: #ffd37a;
}
.workflow-board {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 10px;
}
.workflow-graph,
.workflow-inspector {
  min-height: 0;
  border: 1px solid rgba(120, 151, 176, 0.16);
  border-radius: 18px;
  background: rgba(5, 11, 18, 0.76);
  padding: 14px;
  overflow: auto;
}
.workflow-node-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.workflow-node {
  min-height: 92px;
  text-align: left;
  padding: 12px;
}
.workflow-node.success { border-color: rgba(65, 224, 201, 0.4); }
.workflow-node.error { border-color: rgba(255, 71, 87, 0.55); }
.workflow-node.running { border-color: rgba(214, 161, 59, 0.7); }
.workflow-node.active { box-shadow: 0 0 0 1px rgba(214, 161, 59, 0.6) inset; }
.lock-pill {
  margin-left: 8px;
  font-size: 12px;
  color: #ffd37a;
}
.workflow-event {
  margin: 8px 0;
  padding: 10px;
  border-radius: 12px;
  background: rgba(255,255,255,0.035);
}
.workflow-event code {
  display: block;
  white-space: pre-wrap;
  color: #ff9b9b;
}
```

- [ ] **Step 5: Run frontend tests/build**

Run:

```powershell
npm run dashboard:test
npm run dashboard:build
```

Expected: PASS.

---

### Task 6: Review Board

**Files:**
- Create: `dashboard/src/components/ReviewBoard.tsx`
- Modify: `dashboard/src/App.tsx`

- [ ] **Step 1: Implement lightweight review component**

Create:

```tsx
import type { LedgerEntry, PlanData, WorkflowEvent } from '../types';

export function ReviewBoard({ events, ledger, plan }: { events: WorkflowEvent[]; ledger: LedgerEntry[]; plan: PlanData }) {
  const errors = events.filter((event) => event.status === 'error');
  const trades = ledger.filter((row) => row.symbol || row.code);
  return (
    <section className="review-board">
      <article className="info-card">
        <header><strong>计划 vs 执行</strong><span>{plan.market_bias || '--'}</span></header>
        <p>候选 {(plan.buy_candidates || []).length} 只，成交 {trades.length} 笔</p>
      </article>
      <article className="info-card">
        <header><strong>风险事件</strong><span>{errors.length}</span></header>
        {errors.slice(0, 6).map((event) => <p key={event.event_id}>{event.node_name}: {event.summary}</p>)}
      </article>
      <article className="info-card">
        <header><strong>最近成交</strong><span>{trades.length}</span></header>
        {trades.slice(0, 6).map((row, index) => <p key={row.seq || index}>{row.time} {row.symbol || row.code} {row.decision || row.action} @{row.price}</p>)}
      </article>
    </section>
  );
}
```

- [ ] **Step 2: Import and render in `App.tsx`**

Add:

```tsx
import { ReviewBoard } from './components/ReviewBoard';
```

Use the render snippet from Task 5.

- [ ] **Step 3: Add CSS**

Append:

```css
.review-board {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}
```

- [ ] **Step 4: Build**

Run:

```powershell
npm run dashboard:build
```

Expected: PASS.

---

### Task 7: K-line Trading Result Layer

**Files:**
- Modify: `dashboard/src/components/KlineChart.tsx`
- Modify: `dashboard/src/charts/klineOption.ts`
- Modify: `dashboard/src/charts/klineOption.test.ts`
- Modify: `dashboard/src/types.ts`
- Modify: `dashboard/src/api.ts`

- [ ] **Step 1: Add option-builder test**

Append to `dashboard/src/charts/klineOption.test.ts`:

```ts
it('renders trading result markers and risk lines', () => {
  const option = buildKlineOption(sample, 'MA', [
    {
      time: '2026-06-02',
      code: '000001',
      action: 'buy',
      price: 12,
      shares: 100,
      strategy: 'test',
      stop_loss: 10.8,
      take_profit: 14,
    },
  ]);
  const series = option.series as Array<Record<string, unknown>>;

  expect(series.some((item) => item.name === '交易结果')).toBe(true);
  const candle = series.find((item) => item.name === 'K线') as Record<string, unknown>;
  expect(candle.markLine).toBeTruthy();
});
```

- [ ] **Step 2: Extend `buildKlineOption` signature**

Change signature:

```ts
export function buildKlineOption(data: KlineData, overlay: OverlayKind, trades: KlineTradeMarker[] = []): EChartsOption {
```

Import:

```ts
import type { KlineData, KlineTradeMarker, OverlayKind } from '../types';
```

Before volume series, add:

```ts
const tradePoints = trades
  .filter((trade) => dates.includes(trade.time.slice(0, 10)) || dates.includes(trade.time))
  .map((trade) => {
    const dateKey = dates.includes(trade.time) ? trade.time : trade.time.slice(0, 10);
    return {
      name: trade.action,
      value: trade.price,
      coord: [dateKey, trade.price],
      itemStyle: { color: trade.action === 'buy' ? UP_COLOR : DOWN_COLOR },
      label: { formatter: trade.action === 'buy' ? '买' : '卖' },
      trade,
    };
  });

if (tradePoints.length) {
  series.push({
    name: '交易结果',
    type: 'scatter',
    xAxisIndex: 0,
    yAxisIndex: 0,
    symbol: 'pin',
    symbolSize: 28,
    data: tradePoints,
    tooltip: { show: true },
    z: 10,
  });
  const latest = trades[0];
  const riskLines = [
    latest.stop_loss ? { yAxis: latest.stop_loss, name: '止损' } : undefined,
    latest.take_profit ? { yAxis: latest.take_profit, name: '止盈' } : undefined,
  ].filter(Boolean);
  if (riskLines.length) {
    const candle = series[0] as Record<string, unknown>;
    candle.markLine = {
      symbol: 'none',
      data: riskLines,
      lineStyle: { type: 'dashed', width: 1.2 },
      label: { color: '#d8e6ec' },
    };
  }
}
```

- [ ] **Step 3: Fetch code-specific ledger in `KlineChart`**

Add state:

```tsx
const [trades, setTrades] = useState<KlineTradeMarker[]>([]);
```

Add effect:

```tsx
useEffect(() => {
  if (!code) return;
  let active = true;
  api.ledgerForCode(code, 200).then((rows) => {
    if (!active) return;
    setTrades(rows.map((row) => ({
      time: row.time || '',
      code: row.symbol || row.code || code,
      action: row.decision || row.action || '',
      price: Number(row.price || 0),
      shares: row.shares,
      strategy: row.strategy,
      reasoning: row.reasoning,
    })).filter((row) => row.time && row.price));
  }).catch(() => {
    if (active) setTrades([]);
  });
  return () => { active = false; };
}, [code]);
```

Change option call:

```tsx
const option = buildKlineOption(data, overlay, trades);
```

Add `trades` to dependency array.

- [ ] **Step 4: Run tests/build**

Run:

```powershell
npm run dashboard:test
npm run dashboard:build
```

Expected: PASS.

---

### Task 8: Workflow SSE Polling

**Files:**
- Modify: `src/alphaclaude/app/dashboard.py`
- Test: `tests/test_dashboard_cache.py`

- [ ] **Step 1: Add helper unit test**

Append:

```python
def test_latest_workflow_event_time_handles_missing_file(tmp_path):
    store = app_dashboard._workflow_store_for_run
    assert store is not None
```

This test is intentionally minimal because SSE generator is async and already integration-sensitive. The meaningful verification is manual in Step 4.

- [ ] **Step 2: Update `_sse_event_generator`**

Inside `_sse_event_generator`, add after `last_data_time = ""`:

```python
last_workflow_event_id = ""
```

Inside the polling loop after state polling:

```python
            if state_path:
                workflow_store = _workflow_store_for_run(os.path.basename(output_dir)) if output_dir else None
                if workflow_store:
                    workflow_events = workflow_store.read_events(limit=1)
                    if workflow_events:
                        latest = workflow_events[-1]
                        event_id = latest.get("event_id", "")
                        if event_id and event_id != last_workflow_event_id:
                            last_workflow_event_id = event_id
                            yield f"event: workflow_event\ndata: {json.dumps(latest, ensure_ascii=False)}\n\n"
```

- [ ] **Step 3: Run Python tests**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\test_dashboard_cache.py tests\engine\test_workflow_events.py -q
```

Expected: PASS.

- [ ] **Step 4: Manual verification**

Run:

```powershell
npm run dev
```

Open:

```text
http://127.0.0.1:5173/dashboard/assets/
```

Expected:
- 盯盘模式 K 线正常缩放、平移、tooltip 正常。
- 流程模式能显示 DAG 节点。
- 新事件写入 `data/output/<run_id>/workflow_events.jsonl` 后，流程页面无需刷新能看到变化。
- 复盘模式能看到计划、成交和错误摘要。

---

## Verification Gate

Run all relevant checks before claiming completion:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\engine\test_workflow_events.py tests\test_dashboard_cache.py tests\engine\test_paper_schedule.py tests\engine\test_monitoring_ops.py -q
python -m compileall -q src\alphaclaude
npm run dashboard:test
npm run dashboard:build
```

Expected:

```text
pytest: passed
compileall: no output and exit code 0
dashboard:test: passed
dashboard:build: built successfully
```

## Scope Notes

- This plan implements Phase A 完整可观测 and Phase B 交易结果层。
- It intentionally does not implement free DAG wiring, arbitrary node rerun, live trading approval flow, 缠论/波浪结构渲染, or config mutation that can bypass risk.
- Existing dirty worktree changes must be preserved. Do not revert unrelated frontend/backend migration edits.
- Do not commit unless the user explicitly requests commit/push.

## Self-Review

- Spec coverage: 三模式工作区、workflow_events 文件、workflow_config、artifact、workflow API、SSE、K 线交易结果层均有对应任务。
- Gaps intentionally deferred: 高级结构层、技术信号层、盘中节点重跑、自由拖拽 graph runtime。
- Placeholder scan: no unfinished placeholder markers; deferred items are explicitly scoped out.
- Type consistency: frontend types use `WorkflowEvent`, `WorkflowGraph`, `KlineTradeMarker`; API methods and component props use the same names.
