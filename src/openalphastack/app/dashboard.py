"""Dashboard routes, SSE stream, and K-line cache helpers."""
import asyncio
import json
import logging
import os
import threading
import argparse
from datetime import datetime, time, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from openalphastack.demo_data import dashboard_ledger, dashboard_plan, dashboard_state

from openalphastack.engine import run_registry
from openalphastack.engine import cli as engine_cli
from openalphastack.engine.agent_event import validate_agent_events
from openalphastack.engine.trading_calendar import is_trading_day, non_trading_reason
from openalphastack.engine.workflow_events import (
    WORKFLOW_STAGES,
    WorkflowEventStore,
    default_workflow_edges,
    workflow_stage_id,
)
from openalphastack.paths import DATA_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)
router = APIRouter()
# === Dashboard API ===

OUTPUT_BASE = os.path.join(str(PROJECT_ROOT), "data", "output")
DASHBOARD_DIR = str(PROJECT_ROOT / "dashboard")
DASHBOARD_DIST_DIR = str(PROJECT_ROOT / "dashboard" / "dist")
DASHBOARD_ASSETS_DIR = str(PROJECT_ROOT / "dashboard" / "dist" / "assets")
KLINE_CACHE_DIR = str(DATA_DIR / "cache" / "kline")
LEGACY_MINUTE_CACHE_DIR = str(DATA_DIR / "cache" / "minute")
MINUTE_CACHE_DIR = LEGACY_MINUTE_CACHE_DIR
KLINE_PERIODS = {"day", "week", "month", "1m", "5m", "15m", "60m"}
MINUTE_PERIODS = {"1m", "5m", "15m", "60m"}
RESAMPLE_RULES = {"5m": "5min", "15m": "15min", "60m": "60min"}
DEMO_RUN_ID = "demo_run"


class WorkflowGraphNodeModel(BaseModel):
    id: str
    name: str
    status: str = "idle"
    summary: str = ""
    last_event_id: str = ""
    phase: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int | float = 0
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    artifact_dir: str = ""


class WorkflowGraphEdgeModel(BaseModel):
    from_: str = Field(alias="from")
    to: str
    kind: Literal["data", "sequence"] = "sequence"
    label: str = ""
    refs: list[str] = Field(default_factory=list)
    required: bool = True


class WorkflowGraphModel(BaseModel):
    run_id: str
    nodes: list[WorkflowGraphNodeModel]
    edges: list[WorkflowGraphEdgeModel]
    run_status: str = ""
    is_alive: bool = False
    process_id: int | None = None
    data_time: str = ""
    observation_mode: bool = False
    observation_reason: str = ""
    calendar_date: str = ""
    display_date: str = ""
    is_trading_day: bool = True
    market_status: Literal["trading", "closed", "stale"] = "trading"
    market_message: str = ""


# SSE event queues: one per connected client
_sse_queues: list[asyncio.Queue] = []
_sse_lock = threading.Lock()
_sse_shutdown = False


def reset_sse_shutdown() -> None:
    """Mark Dashboard SSE streams as open for a new app lifespan."""
    global _sse_shutdown
    _sse_shutdown = False


def _arm_forced_exit_timer(timeout_seconds: float = 3.0) -> threading.Timer:
    """Force process exit if shutdown hangs past the grace period."""
    import os as _os

    timer = threading.Timer(timeout_seconds, _os._exit, [0])
    timer.daemon = True
    timer.start()
    return timer


def _get_active_output_dir() -> str | None:
    """Return the most recent paper run's output directory, or None."""
    if not os.path.isdir(OUTPUT_BASE):
        return None
    paper_dirs = sorted(
        [d for d in os.listdir(OUTPUT_BASE) if d.startswith("paper_")],
        reverse=True,
    )
    if not paper_dirs:
        return None
    return os.path.join(OUTPUT_BASE, paper_dirs[0])


def _get_run_output_dir(run_id: str | None = None) -> str | None:
    """Return a safe output directory for a run id, or the active paper run."""
    if run_id == DEMO_RUN_ID:
        return None
    if not run_id or run_id == "active":
        return _get_active_output_dir()

    output_root = os.path.abspath(OUTPUT_BASE)
    candidate = os.path.abspath(os.path.join(output_root, run_id))
    if candidate != output_root and candidate.startswith(output_root + os.sep) and os.path.isdir(candidate):
        return candidate
    return None


def _workflow_store_for_run(run_id: str | None = None) -> WorkflowEventStore | None:
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return None
    return WorkflowEventStore(output_dir, run_id=os.path.basename(output_dir))


def _workflow_runtime_meta(run_id: str) -> dict:
    try:
        record = run_registry.get_run(run_id)
        state = _read_json(os.path.join(record.run_dir, "state.json")) or {}
        engine_meta = state.get("engine_meta", {})
        data_time = state.get("data_time", "")
        return {
            "run_status": record.status,
            "is_alive": record.is_alive,
            "process_id": record.process_id,
            "data_time": data_time,
            "observation_mode": record.observation_mode,
            "observation_reason": engine_meta.get("observation_reason", ""),
            **_workflow_calendar_meta(run_id, data_time),
        }
    except Exception:
        return {
            "run_status": "unknown",
            "is_alive": False,
            "process_id": None,
            "data_time": "",
            "observation_mode": False,
            "observation_reason": "",
            **_workflow_calendar_meta(run_id, ""),
        }


def _date_part(value: str | None) -> str:
    if not value:
        return ""
    import re

    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value))
    return match.group(0) if match else ""


def _workflow_event_display_date(graph: dict[str, Any]) -> str:
    dates = [
        _date_part(str(node.get("ended_at") or node.get("started_at") or ""))
        for node in graph.get("nodes", [])
        if isinstance(node, dict)
    ]
    dates = [item for item in dates if item]
    return max(dates) if dates else ""


def _workflow_calendar_meta(run_id: str, data_time: str | None, event_date: str = "") -> dict[str, Any]:
    today = datetime.now().date()
    calendar_date = today.isoformat()
    is_open_day = is_trading_day(today)
    data_date = _date_part(data_time)
    run_date = _date_part(run_id.replace("T", " "))
    display_date = data_date or event_date or run_date

    if not is_open_day:
        display_date = event_date or run_date or data_date
        reason = non_trading_reason(today)
        return {
            "calendar_date": calendar_date,
            "display_date": display_date,
            "is_trading_day": False,
            "market_status": "closed",
            "market_message": f"今日休市（{reason}），当前展示最近一次模拟盘记录：{display_date or run_id}",
        }

    if display_date and display_date != calendar_date:
        return {
            "calendar_date": calendar_date,
            "display_date": display_date,
            "is_trading_day": True,
            "market_status": "stale",
            "market_message": f"今天是交易日，但当前查看的是 {display_date} 的模拟盘记录。",
        }

    return {
        "calendar_date": calendar_date,
        "display_date": display_date or calendar_date,
        "is_trading_day": True,
        "market_status": "trading",
        "market_message": "今天是交易日，流程图展示当日模拟盘进度。",
    }


def _state_summary_from_file(run_id: str, state: dict | None) -> dict:
    """Return normalized account metrics for a run state payload."""
    if not state:
        return {}
    cash = state.get("cash", 0)
    positions = state.get("holdings", {})
    position_value = sum(
        p.get("shares", 0) * p.get("current_price", 0)
        for p in positions.values()
    )
    nav = state.get("initial_capital", 100000)
    total = cash + position_value
    return {
        "run_id": run_id,
        "total_asset": round(total, 2),
        "cash": round(cash, 2),
        "position_value": round(position_value, 2),
        "day_pnl": round(total - nav, 2),
        "day_return_pct": round((total - nav) / max(nav, 1) * 100, 2),
        "trade_count": state.get("trade_count", 0),
        "win_count": state.get("win_count", 0),
        "positions": positions,
        "engine_meta": state.get("engine_meta", {}),
        "data_time": state.get("data_time", ""),
    }


def _load_watchlist_items() -> list[dict]:
    state_dir = os.path.join(str(PROJECT_ROOT), "data", "state")
    watchlist_path = os.path.join(state_dir, "watchlist.json")
    if os.path.exists(watchlist_path):
        with open(watchlist_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and isinstance(payload.get("stocks"), dict):
            return [
                {
                    "code": code,
                    "name": info.get("name", code) if isinstance(info, dict) else code,
                    "source": "自选",
                }
                for code, info in payload["stocks"].items()
            ]
        if isinstance(payload, list):
            return payload

    pf_path = os.path.join(state_dir, "portfolio.json")
    if os.path.exists(pf_path):
        with open(pf_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, list) else []
    return []


def _stock_name_map_from_watchlist() -> dict[str, str]:
    names: dict[str, str] = {}
    for item in _load_watchlist_items():
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip()
        if code and name:
            names[code] = name
    return names


def _stock_name(code: str, known_names: dict[str, str]) -> str:
    if not code:
        return ""
    if code in known_names:
        return known_names[code]
    index_names = {
        "000001": "上证指数",
        "399001": "深证成指",
        "399006": "创业板指",
        "000688": "科创50",
        "000300": "沪深300",
        "000905": "中证500",
    }
    if code in index_names:
        return index_names[code]
    try:
        from openalphastack.tools.quote import get_stock_quote
        quote = get_stock_quote(code)
        name = str(quote.get("name") or "").strip()
        if name and name != code:
            known_names[code] = name
            return name
    except Exception:
        return ""
    return ""


def _enrich_plan_stock_names(plan: dict) -> dict:
    known_names = _stock_name_map_from_watchlist()
    for candidate in plan.get("buy_candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("name"):
            continue
        code = str(candidate.get("code") or "").strip()
        name = _stock_name(code, known_names)
        if name:
            candidate["name"] = name
    return plan


def _run_record_summary(record: run_registry.RunRecord) -> dict:
    state = _read_json(record.state_path)
    summary = _state_summary_from_file(record.run_id, state)
    return {
        **record.to_dict(),
        "data_time": summary.get("data_time", ""),
        "total_asset": summary.get("total_asset", 0),
        "cash": summary.get("cash", 0),
        "position_value": summary.get("position_value", 0),
        "trade_count": summary.get("trade_count", 0),
        "holdings_count": len(summary.get("positions", {}) or {}),
        "has_plan": os.path.exists(os.path.join(record.run_dir, "plan.json")),
    }


def _dashboard_run_namespace(mode: str = "paper") -> argparse.Namespace:
    return argparse.Namespace(
        mode=mode,
        capital=100000,
        start=None,
        end=None,
        universe="",
        watchlist="",
        resume=None,
        bar_period=60,
    )


def _read_jsonl(path: str, limit: int = 100) -> list[dict]:
    """Read the last *limit* lines from a JSONL file."""
    if not os.path.exists(path):
        return []
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return lines[-limit:]


def _read_json(path: str) -> dict | None:
    """Read a JSON file, return None if missing or unparseable."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _read_json_any(path: str):
    """Read JSON data of any shape, return None if missing or unparseable."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _demo_state() -> dict:
    return dashboard_state()


def _demo_plan() -> dict:
    return dashboard_plan()


def _demo_ledger(limit: int = 50, code: str = "") -> list[dict]:
    return dashboard_ledger(limit=limit, code=code)


def _demo_workflow_events(limit: int = 500) -> list[dict]:
    rows = [
        {
            "event_id": "demo_wf_001",
            "run_id": DEMO_RUN_ID,
            "phase": "premarket",
            "node_id": "market_snapshot",
            "node_name": "市场快照",
            "status": "success",
            "started_at": "2026-06-04T08:40:00",
            "ended_at": "2026-06-04T08:40:08",
            "duration_ms": 8000,
            "input_refs": ["source.quote.market"],
            "output_refs": ["artifact.market.snapshot"],
            "summary": "指数弱修复，量能温和，适合小仓试错。",
            "error": "",
            "artifact_dir": "demo",
        },
        {
            "event_id": "demo_wf_002",
            "run_id": DEMO_RUN_ID,
            "phase": "premarket",
            "node_id": "agent_research",
            "node_name": "自主 Agent 研判",
            "status": "success",
            "started_at": "2026-06-04T08:40:09",
            "ended_at": "2026-06-04T08:45:30",
            "duration_ms": 321000,
            "input_refs": ["artifact.market.snapshot", "account.state", "rule.skills"],
            "output_refs": ["artifact.agent.research", "artifact.agent.plan_draft"],
            "summary": "Agent 已读取本地准则和 skills，产出盘前计划草案。",
            "error": "",
            "artifact_dir": "demo",
        },
        {
            "event_id": "demo_wf_003",
            "run_id": DEMO_RUN_ID,
            "phase": "premarket",
            "node_id": "risk_validation",
            "node_name": "风控校验",
            "status": "success",
            "started_at": "2026-06-04T08:46:00",
            "ended_at": "2026-06-04T08:46:05",
            "duration_ms": 5000,
            "input_refs": ["artifact.agent.plan_draft"],
            "output_refs": ["plan.risk_report"],
            "summary": "2 个候选通过，单票仓位未超过 25%。",
            "error": "",
            "artifact_dir": "demo",
        },
        {
            "event_id": "demo_wf_004",
            "run_id": DEMO_RUN_ID,
            "phase": "intraday",
            "node_id": "intraday_event_stream",
            "node_name": "关键事件流",
            "status": "success",
            "started_at": "2026-06-04T10:12:00",
            "ended_at": "2026-06-04T10:12:01",
            "duration_ms": 1000,
            "input_refs": ["artifact.fastlane.tick"],
            "output_refs": ["account.ledger", "account.state"],
            "summary": "关键事件: 300913 buy 1000 股，成本线刷新。",
            "error": "",
            "artifact_dir": "demo",
        },
        {
            "event_id": "demo_wf_005",
            "run_id": DEMO_RUN_ID,
            "phase": "postclose",
            "node_id": "trade_attribution",
            "node_name": "交易归因",
            "status": "success",
            "started_at": "2026-06-04T15:17:12",
            "ended_at": "2026-06-04T15:17:12",
            "duration_ms": 0,
            "input_refs": ["review.daily_report", "account.ledger"],
            "output_refs": ["review/trade_attribution.json"],
            "summary": "归因显示今日收益主要来自计划内突破买入。",
            "error": "",
            "artifact_dir": "demo",
        },
    ]
    for row in rows:
        row["stage_id"] = workflow_stage_id(row["node_id"], row["phase"])
    return rows[-limit:]


def _demo_workflow_graph() -> dict:
    latest_by_stage = {event["stage_id"]: event for event in _demo_workflow_events(limit=2000)}
    nodes = []
    for stage_id, stage in WORKFLOW_STAGES.items():
        latest = latest_by_stage.get(stage_id, {})
        nodes.append({
            "id": stage_id,
            "name": stage["name"],
            "status": latest.get("status", "idle"),
            "summary": latest.get("summary", ""),
            "last_event_id": latest.get("event_id", ""),
            "phase": stage["phase"],
        })
    return {"run_id": DEMO_RUN_ID, "nodes": nodes, "edges": default_workflow_edges()}


def _demo_agent_run_timeline(task_id: str) -> dict:
    if task_id != "premarket_plan":
        return {"run_id": DEMO_RUN_ID, "task_id": task_id, "events": [], "tasks": {}, "warnings": ["agent run not found"]}
    events = [
        {
            "event_id": "demo_agent_evt_001",
            "task_id": "market_intel",
            "parent_task_id": "premarket_plan",
            "role": "市场情报",
            "status": "running",
            "started_at": "2026-06-04T08:40:09",
            "ended_at": "",
            "summary": "读取市场快照并检查情绪周期。",
            "input_ref": "tasks/market_intel/input.md",
            "output_ref": "",
            "result_ref": "",
            "error": "",
        },
        {
            "event_id": "demo_agent_evt_002",
            "task_id": "market_intel",
            "parent_task_id": "premarket_plan",
            "role": "市场情报",
            "status": "success",
            "started_at": "",
            "ended_at": "2026-06-04T08:42:20",
            "summary": "市场情绪偏谨慎，量能温和修复。",
            "input_ref": "",
            "output_ref": "tasks/market_intel/output.md",
            "result_ref": "tasks/market_intel/result.json",
            "error": "",
        },
        {
            "event_id": "demo_agent_evt_003",
            "task_id": "candidate_discovery",
            "parent_task_id": "premarket_plan",
            "role": "候选发现",
            "status": "success",
            "started_at": "2026-06-04T08:42:21",
            "ended_at": "2026-06-04T08:44:40",
            "summary": "筛选出 2 个候选并写入证据包。",
            "input_ref": "tasks/candidate_discovery/input.md",
            "output_ref": "tasks/candidate_discovery/output.md",
            "result_ref": "tasks/candidate_discovery/result.json",
            "error": "",
        },
    ]
    tasks = {
        "market_intel": {
            "task_id": "market_intel",
            "parent_task_id": "premarket_plan",
            "role": "市场情报",
            "status": "success",
            "summary": "市场情绪偏谨慎，量能温和修复。",
            "input_ref": "tasks/market_intel/input.md",
            "output_ref": "tasks/market_intel/output.md",
            "result_ref": "tasks/market_intel/result.json",
            "events": events[:2],
        },
        "candidate_discovery": {
            "task_id": "candidate_discovery",
            "parent_task_id": "premarket_plan",
            "role": "候选发现",
            "status": "success",
            "summary": "筛选出 2 个候选并写入证据包。",
            "input_ref": "tasks/candidate_discovery/input.md",
            "output_ref": "tasks/candidate_discovery/output.md",
            "result_ref": "tasks/candidate_discovery/result.json",
            "events": events[2:],
        },
    }
    return {"run_id": DEMO_RUN_ID, "task_id": task_id, "events": events, "tasks": tasks, "warnings": []}


def _demo_kline_payload(code: str, period: str, limit: int) -> dict:
    total = max(80, min(limit, 260))
    minute = period in MINUTE_PERIODS
    start = datetime(2026, 6, 4, 9, 30) if minute else datetime(2026, 2, 3)
    step = timedelta(minutes=1 if period == "1m" else int(period[:-1]) if minute else 1)
    dates = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    price = 29.6 if code == "300913" else 10.2
    for index in range(total):
        current = start + (step * index if minute else timedelta(days=index))
        drift = 0.018 * index
        wave = ((index % 12) - 6) * 0.045
        open_price = price + drift + wave
        close_price = open_price + (0.16 if index % 5 in {1, 2, 3} else -0.08)
        high = max(open_price, close_price) + 0.18
        low = min(open_price, close_price) - 0.16
        dates.append(current.strftime("%Y-%m-%d %H:%M") if minute else current.strftime("%Y-%m-%d"))
        opens.append(round(open_price, 2))
        highs.append(round(high, 2))
        lows.append(round(low, 2))
        closes.append(round(close_price, 2))
        volumes.append(150000 + (index % 18) * 12000 + (90000 if index in {45, 46, 47} else 0))
    return {
        "code": code,
        "source": f"{period}_demo",
        "dates": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    }


def _demo_annotations(code: str, period: str) -> list[dict]:
    payload = _demo_kline_payload(code, period, 120)
    dates = payload["dates"]
    return [
        {
            "id": "demo_support",
            "code": code,
            "period": "all",
            "kind": "level",
            "label": "Demo支撑",
            "tone": "up",
            "price": 29.4 if code == "300913" else 10.1,
            "source": {"event_id": "demo_wf_002", "node_id": "risk_validation", "skill": "pivot", "confidence": 76, "summary": "由最近低点聚类生成的支撑位。"},
        },
        {
            "id": "demo_range",
            "code": code,
            "period": "all",
            "kind": "range",
            "label": "Demo入场区间",
            "tone": "warning",
            "price_min": 30.2 if code == "300913" else 10.4,
            "price_max": 31.2 if code == "300913" else 10.8,
            "source": {"event_id": "demo_wf_002", "node_id": "risk_validation", "skill": "plan", "confidence": 68, "summary": "计划候选的入场区间。"},
        },
        {
            "id": "demo_trend",
            "code": code,
            "period": "all",
            "kind": "trendline",
            "label": "Demo上升趋势线",
            "tone": "neutral",
            "points": [
                {"time": dates[-70], "price": 29.2 if code == "300913" else 10.0},
                {"time": dates[-10], "price": 31.0 if code == "300913" else 10.8},
            ],
            "source": {"event_id": "demo_wf_001", "node_id": "market_snapshot", "skill": "trend", "confidence": 71, "summary": "趋势线仅用于演示结构图层。"},
        },
    ]


def _as_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _normalize_annotation_points(points) -> list[dict]:
    if not isinstance(points, list):
        return []
    normalized = []
    for point in points:
        if not isinstance(point, dict):
            continue
        time = str(point.get("time") or point.get("date") or "").strip()
        price = _as_float(point.get("price"))
        if not time or price is None:
            continue
        normalized.append({
            "time": time,
            "price": price,
            "label": str(point.get("label", "")).strip(),
        })
    return normalized


def _normalize_kline_annotation(raw, *, code: str, period: str, index: int) -> dict | None:
    """Normalize a persisted structure annotation for safe frontend rendering."""
    if not isinstance(raw, dict):
        return None
    item_code = str(raw.get("code") or code).strip()
    item_period = str(raw.get("period") or "all").strip().lower()
    if item_code != code or item_period not in {"all", period}:
        return None

    kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
    if kind not in {"level", "range", "trendline", "segment", "wave", "point"}:
        return None

    points = _normalize_annotation_points(raw.get("points"))
    price = _as_float(raw.get("price"))
    price_min = _as_float(raw.get("price_min") if "price_min" in raw else raw.get("low"))
    price_max = _as_float(raw.get("price_max") if "price_max" in raw else raw.get("high"))
    start_time = str(raw.get("start_time") or raw.get("start") or "").strip()
    end_time = str(raw.get("end_time") or raw.get("end") or "").strip()

    if kind == "level" and price is None:
        return None
    if kind == "range" and (price_min is None or price_max is None):
        return None
    if kind in {"trendline", "segment", "wave"} and len(points) < 2:
        return None
    if kind == "point" and price is None and not points:
        return None

    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return {
        "id": str(raw.get("id") or f"{code}_{period}_{index}"),
        "code": item_code,
        "period": item_period,
        "kind": kind,
        "label": str(raw.get("label") or raw.get("name") or kind).strip(),
        "tone": str(raw.get("tone") or "neutral").strip().lower()
        if str(raw.get("tone") or "neutral").strip().lower() in {"up", "down", "neutral", "warning"}
        else "neutral",
        "price": price,
        "price_min": price_min,
        "price_max": price_max,
        "start_time": start_time,
        "end_time": end_time,
        "points": points,
        "source": {
            "event_id": str(source.get("event_id", "")).strip(),
            "node_id": str(source.get("node_id", "")).strip(),
            "skill": str(source.get("skill", "")).strip(),
            "confidence": _as_float(source.get("confidence")),
            "summary": str(source.get("summary", "")).strip(),
        },
    }


def _extract_annotations_from_json(payload, *, code: str) -> list:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    by_code = payload.get(code)
    if isinstance(by_code, list):
        return by_code
    annotations = payload.get("annotations")
    return annotations if isinstance(annotations, list) else []


def _load_kline_annotations(code: str, period: str, run_id: str | None = None) -> list[dict]:
    """Load structured K-line annotations from the selected run output directory."""
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return []

    candidates = [
        os.path.join(output_dir, "kline_annotations", f"{code}.json"),
        os.path.join(output_dir, "kline_annotations.json"),
    ]
    normalized: list[dict] = []
    for path in candidates:
        payload = _read_json_any(path)
        for raw in _extract_annotations_from_json(payload, code=code):
            item = _normalize_kline_annotation(raw, code=code, period=period, index=len(normalized))
            if item:
                normalized.append(item)
    return normalized


def _write_kline_annotations(output_dir: str, code: str, annotations: list[dict]) -> None:
    annotation_dir = os.path.join(output_dir, "kline_annotations")
    os.makedirs(annotation_dir, exist_ok=True)
    path = os.path.join(annotation_dir, f"{code}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "annotations": annotations}, f, ensure_ascii=False, indent=2)


def _generate_kline_annotations_from_tools(code: str, period: str) -> list[dict]:
    """Generate structure annotations from local rule/skill tools."""
    try:
        from openalphastack.tools.pivot import cluster_levels, find_box_range, find_pivots, find_zhongshu
    except Exception as e:
        logger.warning("Pivot tools unavailable for annotations: %s", e)
        return []

    try:
        df = _load_day_kline_df(code, 160)
    except Exception as e:
        logger.warning("Annotation K-line load failed: %s %s", code, e)
        return []
    if df is None or df.empty or len(df) < 30:
        return []

    dates = df.sort_values("time")["time"].dt.strftime("%Y-%m-%d").tolist()
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    closes = df["close"].astype(float).tolist()
    current = closes[-1]
    annotations: list[dict] = []

    pivots = find_pivots(highs, lows, window=5)
    support_clusters = cluster_levels(pivots.get("pivot_lows", []), tolerance_pct=3.0)
    resistance_clusters = cluster_levels(pivots.get("pivot_highs", []), tolerance_pct=3.0)
    for cluster in support_clusters[:2]:
        annotations.append({
            "id": f"{code}_support_{len(annotations)}",
            "code": code,
            "period": "all",
            "kind": "level",
            "label": f"支撑 {cluster.get('touches', 1)}触",
            "tone": "up",
            "price": cluster.get("price"),
            "source": {
                "node_id": "signal_scan",
                "skill": "pivot",
                "confidence": min(92, 55 + int(cluster.get("touches", 1)) * 8),
                "summary": "由 pivot 低点聚类自动生成。",
            },
        })
    for cluster in resistance_clusters[:2]:
        annotations.append({
            "id": f"{code}_resistance_{len(annotations)}",
            "code": code,
            "period": "all",
            "kind": "level",
            "label": f"压力 {cluster.get('touches', 1)}触",
            "tone": "down",
            "price": cluster.get("price"),
            "source": {
                "node_id": "signal_scan",
                "skill": "pivot",
                "confidence": min(92, 55 + int(cluster.get("touches", 1)) * 8),
                "summary": "由 pivot 高点聚类自动生成。",
            },
        })

    box = find_box_range(df)
    if box.get("signal") == "box_identified":
        annotations.append({
            "id": f"{code}_box_range",
            "code": code,
            "period": "all",
            "kind": "range",
            "label": "箱体区间",
            "tone": "warning",
            "price_min": box.get("box_bottom"),
            "price_max": box.get("box_top"),
            "source": {
                "node_id": "signal_scan",
                "skill": "pivot.box",
                "confidence": 70,
                "summary": f"{box.get('zone', '')}，当前箱体位置 {box.get('position_in_box_pct', '--')}%。",
            },
        })

    zhongshu = find_zhongshu(df)
    if zhongshu.get("signal") == "zhongshu_identified":
        annotations.append({
            "id": f"{code}_zhongshu",
            "code": code,
            "period": "all",
            "kind": "range",
            "label": "中枢区间",
            "tone": "neutral",
            "price_min": zhongshu.get("zhongshu_bottom"),
            "price_max": zhongshu.get("zhongshu_top"),
            "source": {
                "node_id": "signal_scan",
                "skill": "pivot.zhongshu",
                "confidence": 64,
                "summary": f"{zhongshu.get('direction', '')}，{zhongshu.get('buy_point_type', '')}。",
            },
        })

    low_points = pivots.get("pivot_lows", [])[-2:]
    if len(low_points) == 2:
        annotations.append({
            "id": f"{code}_trendline",
            "code": code,
            "period": "all",
            "kind": "trendline",
            "label": "低点趋势线",
            "tone": "up" if current >= low_points[-1]["price"] else "warning",
            "points": [
                {"time": dates[low_points[0]["index"]], "price": low_points[0]["price"]},
                {"time": dates[low_points[1]["index"]], "price": low_points[1]["price"]},
            ],
            "source": {
                "node_id": "signal_scan",
                "skill": "trend",
                "confidence": 62,
                "summary": "由最近两个 pivot low 连接生成，作为趋势结构参考。",
            },
        })
    return annotations


def _ensure_generated_kline_annotations(code: str, period: str, run_id: str | None = None) -> list[dict]:
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return []
    existing = _load_kline_annotations(code, period, run_id)
    if existing:
        return existing
    generated = _generate_kline_annotations_from_tools(code, period)
    if generated:
        _write_kline_annotations(output_dir, code, generated)
    return _load_kline_annotations(code, period, run_id)


def _cache_tree_stats(path: str) -> dict:
    """Return recursive size and file count for a cache tree."""
    total_bytes = 0
    file_count = 0
    newest_mtime = 0.0
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    stat = os.stat(file_path)
                except OSError:
                    continue
                file_count += 1
                total_bytes += stat.st_size
                newest_mtime = max(newest_mtime, stat.st_mtime)
    return {
        "path": path,
        "files": file_count,
        "bytes": total_bytes,
        "mb": round(total_bytes / 1024 / 1024, 3),
        "updated_at": datetime.fromtimestamp(newest_mtime).isoformat() if newest_mtime else "",
    }


def _kline_cache_roots() -> list[str]:
    """Return cache roots that contain Dashboard K-line data only."""
    return [KLINE_CACHE_DIR, LEGACY_MINUTE_CACHE_DIR]


def _kline_cache_stats() -> dict:
    """Return size and file count for all local K-line caches."""
    layers = {
        "kline": _cache_tree_stats(KLINE_CACHE_DIR),
        "legacy_minute": _cache_tree_stats(LEGACY_MINUTE_CACHE_DIR),
    }
    total_files = sum(layer["files"] for layer in layers.values())
    total_bytes = sum(layer["bytes"] for layer in layers.values())
    updated_at = max((layer["updated_at"] for layer in layers.values() if layer["updated_at"]), default="")
    total = {
        "path": KLINE_CACHE_DIR,
        "files": total_files,
        "bytes": total_bytes,
        "mb": round(total_bytes / 1024 / 1024, 3),
        "updated_at": updated_at,
        "layers": layers,
    }
    return {"kline_cache": total, "minute_cache": total}


def _assert_safe_cache_path(path: str) -> str:
    """Resolve and validate that a cache path stays under data/cache."""
    target = os.path.abspath(path)
    root = os.path.abspath(str(DATA_DIR / "cache"))
    if target != root and not target.startswith(root + os.sep):
        raise RuntimeError(f"Refusing unsafe cache path: {target}")
    return target


def _clear_kline_cache() -> dict:
    """Delete files under K-line cache roots only."""
    removed = 0
    bytes_removed = 0
    for root_path in _kline_cache_roots():
        target = _assert_safe_cache_path(root_path)
        if not os.path.isdir(target):
            continue
        for root, _, files in os.walk(target):
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    size = os.stat(file_path).st_size
                    os.remove(file_path)
                except OSError:
                    continue
                removed += 1
                bytes_removed += size
    return {
        "removed_files": removed,
        "removed_bytes": bytes_removed,
        "removed_mb": round(bytes_removed / 1024 / 1024, 3),
        **_kline_cache_stats(),
    }


def _minute_cache_stats() -> dict:
    """Backward-compatible alias for Dashboard cache stats."""
    return _kline_cache_stats()


def _clear_minute_cache() -> dict:
    """Backward-compatible alias that now clears all K-line cache levels."""
    return _clear_kline_cache()


def _stock_prefix(code: str) -> str:
    return "sh" if code.startswith(("5", "6", "9")) else "sz"


def _kline_cache_path(period: str, code: str) -> str:
    suffix = "json" if period in ("day", "week", "month") else "parquet"
    return os.path.join(KLINE_CACHE_DIR, period, f"{code}.{suffix}")


def _kline_now() -> datetime:
    return datetime.now()


def _last_kline_time(df):
    if df is None or df.empty:
        return None
    return df["time"].max()


def _merge_kline_df(cached, fetched):
    import pandas as pd

    frames = [df for df in (cached, fetched) if df is not None and not df.empty]
    if not frames:
        return None
    merged = pd.concat(frames, ignore_index=True)
    merged["time"] = pd.to_datetime(merged["time"])
    return (
        merged[["time", "open", "high", "low", "close", "volume"]]
        .sort_values("time")
        .drop_duplicates(subset=["time"], keep="last")
        .reset_index(drop=True)
    )


def _is_day_kline_stale(df) -> bool:
    last_time = _last_kline_time(df)
    if last_time is None:
        return True
    now = _kline_now()
    today = now.date()
    if not is_trading_day(today):
        return False
    return last_time.date() < today


def _previous_trading_day(day):
    from datetime import timedelta

    candidate = day - timedelta(days=1)
    for _ in range(10):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return day - timedelta(days=1)


def _expected_latest_minute_time(now: datetime) -> datetime | None:
    if not is_trading_day(now.date()):
        previous = _previous_trading_day(now.date())
        return datetime.combine(previous, time(15, 0))
    current = now.time()
    if current < time(9, 30):
        previous = _previous_trading_day(now.date())
        return datetime.combine(previous, time(15, 0))
    if current <= time(11, 30):
        return now.replace(second=0, microsecond=0)
    if current < time(13, 0):
        return now.replace(hour=11, minute=30, second=0, microsecond=0)
    if current <= time(15, 0):
        return now.replace(second=0, microsecond=0)
    return now.replace(hour=15, minute=0, second=0, microsecond=0)


def _is_minute_kline_stale(df) -> bool:
    last_time = _last_kline_time(df)
    if last_time is None:
        return True
    expected = _expected_latest_minute_time(_kline_now())
    return expected is not None and last_time.to_pydatetime() < expected


def _df_to_kline_payload(code: str, df, source: str) -> dict:
    """Convert an OHLCV DataFrame to the Dashboard API shape."""
    df = df.sort_values("time").copy()
    is_minute = any((df["time"].dt.hour != 0) | (df["time"].dt.minute != 0))
    times = df["time"]
    if is_minute:
        dates = times.dt.strftime("%Y-%m-%d %H:%M").tolist()
    else:
        dates = times.dt.strftime("%Y-%m-%d").tolist()
    return {
        "code": code,
        "source": source,
        "dates": dates,
        "open": df["open"].astype(float).round(4).tolist(),
        "high": df["high"].astype(float).round(4).tolist(),
        "low": df["low"].astype(float).round(4).tolist(),
        "close": df["close"].astype(float).round(4).tolist(),
        "volume": df["volume"].astype(float).round(2).tolist(),
    }


def _read_kline_json(path: str):
    import pandas as pd

    data = _read_json(path)
    if not data or not data.get("rows"):
        return None
    df = pd.DataFrame(data["rows"])
    if df.empty:
        return None
    df["time"] = pd.to_datetime(df["time"])
    return df[["time", "open", "high", "low", "close", "volume"]]


def _write_kline_json(path: str, df) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = df.copy()
    rows["time"] = rows["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "updated_at": datetime.now().isoformat(),
        "rows": rows[["time", "open", "high", "low", "close", "volume"]].to_dict("records"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _read_kline_parquet(path: str):
    import pandas as pd

    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    if df.empty:
        return None
    df["time"] = pd.to_datetime(df["time"])
    return df[["time", "open", "high", "low", "close", "volume"]].sort_values("time")


def _write_kline_parquet(path: str, df) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df[["time", "open", "high", "low", "close", "volume"]].sort_values("time").to_parquet(path, index=False)


def _fetch_tencent_day_df(code: str, limit: int):
    import pandas as pd
    import requests as _r

    symbol = f"{_stock_prefix(code)}{code}"
    urls = [
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{limit},qfq",
        "http://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"
        f"?param={symbol},day,,,{limit},qfq",
    ]
    raw_rows = []
    last_error = None
    for url in urls:
        try:
            resp = _r.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            raw_rows = resp.json().get("data", {}).get(symbol, {}).get("qfqday", [])
            if raw_rows:
                break
        except Exception as e:
            last_error = e
    if not raw_rows and last_error:
        raise last_error
    rows = []
    for row in raw_rows:
        rows.append({
            "time": pd.to_datetime(row[0]),
            "open": float(row[1]),
            "close": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "volume": float(row[5]),
        })
    return pd.DataFrame(rows)


def _fetch_tencent_minute_df(code: str, limit: int):
    import pandas as pd
    import requests as _r

    symbol = f"{_stock_prefix(code)}{code}"
    request_limit = max(limit, 320)
    urls = [
        "http://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"
        f"?param={symbol},m1,,{request_limit}",
        "https://web.ifzq.gtimg.cn/appstock/app/kline/mkline"
        f"?param={symbol},m1,,{request_limit}",
        "https://web3.ifzq.gtimg.cn/appstock/app/kline/mkline"
        f"?param={symbol},m1,,{request_limit}",
    ]
    raw_rows = []
    last_error = None
    for url in urls:
        try:
            resp = _r.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            raw_rows = resp.json().get("data", {}).get(symbol, {}).get("m1", [])
            if raw_rows:
                break
        except Exception as e:
            last_error = e
    if not raw_rows and last_error:
        raise last_error
    rows = []
    for row in raw_rows:
        rows.append({
            "time": pd.to_datetime(row[0]),
            "open": float(row[1]),
            "close": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "volume": float(row[5]),
        })
    return pd.DataFrame(rows)


def _resample_ohlcv(df, rule: str):
    resampled = (
        df.sort_values("time")
        .set_index("time")
        .resample(rule)
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        })
        .dropna()
        .reset_index()
    )
    return resampled


def _load_day_kline_df(code: str, limit: int):
    path = _kline_cache_path("day", code)
    df = _read_kline_json(path)
    if df is None or len(df) < min(limit, 60) or _is_day_kline_stale(df):
        try:
            fetched = _fetch_tencent_day_df(code, max(limit, 260))
            if not fetched.empty:
                df = _merge_kline_df(df, fetched)
                _write_kline_json(path, df)
        except Exception as e:
            logger.warning("Day K-line fetch failed: %s %s", code, e)
    return df.sort_values("time").tail(limit) if df is not None and not df.empty else None


def _load_week_kline_df(code: str, limit: int):
    path = _kline_cache_path("week", code)
    df = _read_kline_json(path)
    day_df = _load_day_kline_df(code, max(limit * 7, 260))
    if day_df is None or day_df.empty:
        return df.sort_values("time").tail(limit) if df is not None and not df.empty else None
    refreshed = _resample_ohlcv(day_df, "W")
    if not refreshed.empty:
        df = refreshed
        _write_kline_json(path, df)
    elif df is None or len(df) < min(limit, 30):
        return None
    return df.sort_values("time").tail(limit) if df is not None and not df.empty else None


def _load_month_kline_df(code: str, limit: int):
    path = _kline_cache_path("month", code)
    df = _read_kline_json(path)
    day_df = _load_day_kline_df(code, max(limit * 31, 520))
    if day_df is None or day_df.empty:
        return df.sort_values("time").tail(limit) if df is not None and not df.empty else None
    refreshed = _resample_ohlcv(day_df, "ME")
    if not refreshed.empty:
        df = refreshed
        _write_kline_json(path, df)
    elif df is None or len(df) < min(limit, 12):
        return None
    return df.sort_values("time").tail(limit) if df is not None and not df.empty else None


def _load_1m_kline_df(code: str, limit: int):
    path = _kline_cache_path("1m", code)
    df = _read_kline_parquet(path)
    if df is None:
        for legacy_path in (
            os.path.join(LEGACY_MINUTE_CACHE_DIR, f"{code}_1m.parquet"),
            os.path.join(LEGACY_MINUTE_CACHE_DIR, f"{code}.parquet"),
        ):
            df = _read_kline_parquet(legacy_path)
            if df is not None:
                break
    if df is None or len(df) < min(limit, 120) or _is_minute_kline_stale(df):
        try:
            fetched = _fetch_tencent_minute_df(code, max(limit, 800))
            if not fetched.empty:
                df = _merge_kline_df(df, fetched)
                _write_kline_parquet(path, df)
        except Exception as e:
            logger.warning("Minute K-line fetch failed: %s %s", code, e)
    elif df is not None and not os.path.exists(path):
        _write_kline_parquet(path, df)
    return df.sort_values("time").tail(limit) if df is not None and not df.empty else None


def _load_minute_kline_df(code: str, period: str, limit: int):
    if period == "1m":
        return _load_1m_kline_df(code, limit)
    path = _kline_cache_path(period, code)
    df = _read_kline_parquet(path)
    base_limit = max(limit * int(period[:-1]), 800)
    base_df = _load_1m_kline_df(code, base_limit)
    if base_df is None or base_df.empty:
        if df is None:
            legacy_path = os.path.join(LEGACY_MINUTE_CACHE_DIR, f"{code}_{period}.parquet")
            df = _read_kline_parquet(legacy_path)
    else:
        df = _resample_ohlcv(base_df, RESAMPLE_RULES[period])
        if not df.empty:
            _write_kline_parquet(path, df)
    return df.sort_values("time").tail(limit) if df is not None and not df.empty else None


def _broadcast_sse(event_type: str, data: dict) -> None:
    """Push an SSE event to all connected Dashboard clients."""
    payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)


def _shutdown_sse() -> None:
    """Wake all SSE generators so they exit gracefully."""
    global _sse_shutdown
    _sse_shutdown = True
    with _sse_lock:
        for q in _sse_queues:
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass
        _sse_queues.clear()


arm_forced_exit_timer = _arm_forced_exit_timer
shutdown_sse = _shutdown_sse


def _build_nav_sse(state: dict, run_id: str = "") -> str:
    """Build an SSE 'nav' event string from a state.json dict."""
    nav = state.get("initial_capital", 100000)
    cash = state.get("cash", 0)
    positions = state.get("holdings", {})
    position_value = sum(
        p.get("shares", 0) * p.get("current_price", 0)
        for p in positions.values()
    )
    total = cash + position_value
    pnl = total - nav
    return f"event: nav\ndata: {json.dumps({'run_id': run_id, 'total_asset': round(total, 2), 'cash': round(cash, 2), 'position_value': round(position_value, 2), 'day_pnl': round(pnl, 2), 'day_return_pct': round(pnl / max(nav, 1) * 100, 2), 'positions': positions, 'data_time': state.get('data_time', '')}, ensure_ascii=False)}\n\n"


async def _sse_event_generator(request: Request):
    """SSE stream for Dashboard real-time updates.

    Polls state.json data_time every 2s for changes from the engine subprocess.
    Also relays events pushed via _broadcast_sse() (trade, emergency, plan_updated).
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    with _sse_lock:
        _sse_queues.append(q)

    last_data_time = ""
    last_workflow_event_id = ""
    try:
        # Send initial state snapshot
        output_dir = _get_active_output_dir()
        run_id = os.path.basename(output_dir) if output_dir else ""
        state_path = os.path.join(output_dir, "state.json") if output_dir else ""
        if state_path:
            state = _read_json(state_path)
            if state:
                last_data_time = state.get("data_time", "")
                yield _build_nav_sse(state, run_id)

        yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'time': datetime.now().isoformat()})}\n\n"

        while not _sse_shutdown:
            if await request.is_disconnected():
                break
            try:
                msg = await asyncio.wait_for(q.get(), timeout=2.0)
                if msg is None:  # shutdown sentinel
                    break
                yield msg
            except asyncio.TimeoutError:
                pass  # poll state.json below

            # Poll state.json for engine-driven changes (engine is a subprocess,
            # can't call _broadcast_sse directly)
            if _sse_shutdown:
                break
            if state_path:
                state = _read_json(state_path)
                if state:
                    dt = state.get("data_time", "")
                    if dt and dt != last_data_time:
                        last_data_time = dt
                        yield _build_nav_sse(state, run_id)
            if output_dir:
                workflow_store = _workflow_store_for_run(os.path.basename(output_dir))
                if workflow_store:
                    workflow_events = workflow_store.read_events(limit=1)
                    if workflow_events:
                        latest = workflow_events[-1]
                        event_id = latest.get("event_id", "")
                        if event_id and event_id != last_workflow_event_id:
                            last_workflow_event_id = event_id
                            yield f"event: workflow_event\ndata: {json.dumps(latest, ensure_ascii=False)}\n\n"
    finally:
        with _sse_lock:
            if q in _sse_queues:
                _sse_queues.remove(q)


@router.get("/api/stream")
async def dashboard_sse(request: Request):
    return StreamingResponse(
        _sse_event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/state")
async def api_state(run_id: str | None = None):
    if run_id == DEMO_RUN_ID:
        return _demo_state()
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return _demo_state()
    state = _read_json(os.path.join(output_dir, "state.json"))
    if not state:
        return JSONResponse({"error": "state.json not found"}, status_code=404)
    return _state_summary_from_file(os.path.basename(output_dir), state)


@router.get("/api/plan")
async def api_plan(run_id: str | None = None):
    if run_id == DEMO_RUN_ID:
        return _enrich_plan_stock_names(_demo_plan())
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return _enrich_plan_stock_names(_demo_plan())
    plan = _read_json(os.path.join(output_dir, "plan.json"))
    if not plan:
        return JSONResponse({"error": "plan.json not found"}, status_code=404)
    return _enrich_plan_stock_names(plan)


@router.get("/api/ledger")
async def api_ledger(limit: int = 50, code: str = "", run_id: str | None = None):
    if run_id == DEMO_RUN_ID:
        return _demo_ledger(limit=limit, code=code)
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return _demo_ledger(limit=limit, code=code)
    entries = _read_jsonl(os.path.join(output_dir, "ledger.jsonl"), limit=limit)
    if code:
        entries = [e for e in entries if e.get("symbol", "") == code or e.get("code", "") == code]
    return entries


@router.get("/api/quote/{code}")
async def api_quote(code: str):
    try:
        from openalphastack.tools.quote import get_stock_quote
        result = get_stock_quote(code)
        return result if result else {"error": f"No data for {code}"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/kline/{code}")
async def api_kline(code: str, period: str = "day", limit: int = 200):
    """Return OHLCV data for ECharts candlestick rendering.

    Periods:
      - day: cache-first Tencent daily K-line
      - week/month: resampled from day K-line
      - 1m: cache-first Tencent minute K-line / legacy parquet fallback
      - 5m/15m/60m: resampled from 1m K-line
    """
    period = period.lower()
    limit = max(1, min(int(limit), 2000))
    if period not in KLINE_PERIODS:
        return JSONResponse({"error": f"Unsupported period: {period}"}, status_code=400)
    if not _get_active_output_dir():
        return _demo_kline_payload(code, period, limit)

    try:
        if period == "day":
            df = _load_day_kline_df(code, limit)
        elif period == "week":
            df = _load_week_kline_df(code, limit)
        elif period == "month":
            df = _load_month_kline_df(code, limit)
        else:
            df = _load_minute_kline_df(code, period, limit)
    except Exception as e:
        logger.warning("K-line load failed: %s %s %s", code, period, e)
        df = None

    if df is None or df.empty:
        if not _get_active_output_dir():
            return _demo_kline_payload(code, period, limit)
        return JSONResponse({"error": f"No K-line data for {code} {period}"}, status_code=404)

    return _df_to_kline_payload(code, df, f"{period}_cache_chain")


@router.get("/api/kline/{code}/annotations")
async def api_kline_annotations(code: str, period: str = "day", run_id: str | None = None):
    """Return Agent-produced structured K-line annotations for optional chart layers."""
    period = period.lower()
    if period not in KLINE_PERIODS:
        return JSONResponse({"error": f"Unsupported period: {period}"}, status_code=400)
    if run_id == DEMO_RUN_ID or not _get_run_output_dir(run_id):
        return {"code": code, "period": period, "annotations": _demo_annotations(code, period)}
    return {"code": code, "period": period, "annotations": _ensure_generated_kline_annotations(code, period, run_id)}


@router.post("/api/kline/{code}/annotations/generate")
async def api_kline_annotations_generate(code: str, period: str = "day", run_id: str | None = None):
    """Regenerate local skill-derived K-line structure annotations for the active run."""
    period = period.lower()
    if period not in KLINE_PERIODS:
        return JSONResponse({"error": f"Unsupported period: {period}"}, status_code=400)
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return {"code": code, "period": period, "annotations": _demo_annotations(code, period), "demo": True}
    generated = _generate_kline_annotations_from_tools(code, period)
    _write_kline_annotations(output_dir, code, generated)
    return {"code": code, "period": period, "annotations": _load_kline_annotations(code, period, run_id), "generated": len(generated)}


@router.get("/api/technical/{code}")
async def api_technical(code: str, indicator: str = "all"):
    try:
        from openalphastack.tools.technical import get_technical
        result = get_technical(code, indicator=indicator)
        return result if result else {"error": f"No data for {code}"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/watchlist")
async def api_watchlist():
    try:
        items = _load_watchlist_items()
        if items:
            return items
        if not _get_active_output_dir():
            return [{"code": "300913", "source": "demo"}, {"code": "000001", "name": "上证指数", "source": "demo"}]
        return []
    except Exception:
        return []


@router.get("/api/cache/status")
async def api_cache_status():
    return _kline_cache_stats()


@router.post("/api/cache/kline/clear")
async def api_cache_kline_clear():
    try:
        return _clear_kline_cache()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/cache/minute/clear")
async def api_cache_minute_clear():
    return await api_cache_kline_clear()


@router.get("/api/runs")
async def api_runs(mode: str = "all"):
    normalized_mode = mode.lower().strip()
    if normalized_mode == "all":
        records = run_registry.list_runs()
    elif normalized_mode in {"paper", "live", "agent"}:
        records = run_registry.list_runs(normalized_mode)
    else:
        return JSONResponse({"error": f"Unsupported mode: {mode}"}, status_code=400)

    runs = [
        _run_record_summary(record)
        for record in records
        if record.mode in {"paper", "live", "agent"}
    ]
    if not runs:
        demo_state = _demo_state()
        return {
            "runs": [{
                "run_id": DEMO_RUN_ID,
                "mode": "demo",
                "status": "demo",
                "is_alive": False,
                "process_id": None,
                "data_time": demo_state["data_time"],
                "total_asset": demo_state["total_asset"],
                "cash": demo_state["cash"],
                "position_value": demo_state["position_value"],
                "trade_count": demo_state["trade_count"],
                "holdings_count": len(demo_state["positions"]),
                "has_plan": True,
                "live_locked": True,
            }],
            "selected_run_id": DEMO_RUN_ID,
        }
    active = next((run for run in runs if run.get("is_alive")), runs[0])
    return {"runs": runs, "selected_run_id": active["run_id"]}


@router.post("/api/runs/start")
async def api_run_start(request: Request):
    payload = await request.json()
    mode = str(payload.get("mode") or "paper").strip().lower()
    if mode == "live":
        return JSONResponse(
            {"error": "实盘未准入：BrokerAdapter、人工确认、订单幂等和安全闸门完成前禁止从 Dashboard 启动。"},
            status_code=423,
        )
    if mode != "paper":
        return JSONResponse({"error": f"Unsupported mode: {mode}"}, status_code=400)
    info = engine_cli.start_daemon(_dashboard_run_namespace(mode="paper"))
    return {"run": info}


@router.post("/api/runs/{run_id}/resume")
async def api_run_resume(run_id: str):
    if run_id.startswith("live_"):
        return JSONResponse(
            {"error": "实盘未准入：当前 Dashboard 只允许查看已有 live run。"},
            status_code=423,
        )
    info = engine_cli.resume_run_daemon(run_id, _dashboard_run_namespace(mode="paper"))
    return {"run": info}


@router.post("/api/runs/{run_id}/stop")
async def api_run_stop(run_id: str):
    if run_id == DEMO_RUN_ID:
        return JSONResponse({"error": "Demo run cannot be stopped"}, status_code=409)
    if run_id.startswith("live_"):
        return JSONResponse(
            {"error": "实盘未准入：当前 Dashboard 只允许查看已有 live run。"},
            status_code=423,
        )
    result = run_registry.stop_run(run_id)
    return {"run": result.to_dict()}


@router.get("/api/engine/status")
async def api_engine_status(run_id: str | None = None):
    if run_id == DEMO_RUN_ID:
        demo_state = _demo_state()
        return {
            "run_id": DEMO_RUN_ID,
            "status": "demo",
            "is_alive": False,
            "process_id": None,
            "observation_mode": True,
            "observation_reason": "当前没有真实模拟盘，Dashboard 使用只读 Demo 数据。",
            "data_time": demo_state["data_time"],
            "has_plan": True,
        }
    if run_id and run_id != "active":
        try:
            records = [run_registry.get_run(run_id)]
        except run_registry.RunNotFound as e:
            return JSONResponse({"error": str(e), "run_id": e.run_id}, status_code=404)
    else:
        records = run_registry.list_runs("paper")
    record = records[0] if records else None
    output_dir = record.run_dir if record else _get_active_output_dir()
    run_id = record.run_id if record else (os.path.basename(output_dir) if output_dir else None)
    state = _read_json(os.path.join(output_dir, "state.json")) if output_dir else None
    engine_meta = record.engine_meta if record else (state.get("engine_meta", {}) if state else {})
    if not output_dir and not record:
        return {
            "run_id": DEMO_RUN_ID,
            "status": "demo",
            "is_alive": False,
            "process_id": None,
            "observation_mode": True,
            "observation_reason": "当前没有真实模拟盘，Dashboard 使用只读 Demo 数据。",
            "data_time": _demo_state()["data_time"],
            "has_plan": True,
        }
    return {
        "run_id": run_id,
        "status": record.status if record else engine_meta.get("status", "unknown"),
        "is_alive": record.is_alive if record else False,
        "process_id": record.process_id if record else None,
        "observation_mode": record.observation_mode if record else engine_meta.get("observation_mode", False),
        "observation_reason": engine_meta.get("observation_reason", ""),
        "data_time": state.get("data_time", "") if state else "",
        "has_plan": os.path.exists(os.path.join(output_dir, "plan.json")) if output_dir else False,
    }


@router.get("/api/workflow/runs/{run_id}/events")
async def api_workflow_events(run_id: str, limit: int = 500):
    if run_id in {"active", DEMO_RUN_ID} and not _get_active_output_dir():
        safe_limit = max(1, min(int(limit), 2000))
        return {"run_id": DEMO_RUN_ID, "events": _demo_workflow_events(limit=safe_limit)}
    store = _workflow_store_for_run(run_id)
    if not store:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)
    safe_limit = max(1, min(int(limit), 2000))
    return {"run_id": store.run_id, "events": store.read_events(limit=safe_limit)}


@router.get("/api/workflow/runs/{run_id}/graph", response_model=WorkflowGraphModel)
async def api_workflow_graph(run_id: str) -> dict[str, Any] | JSONResponse:
    if run_id in {"active", DEMO_RUN_ID} and not _get_active_output_dir():
        graph = _demo_workflow_graph()
        data_time = _demo_state()["data_time"]
        event_date = _workflow_event_display_date(graph)
        graph.update({
            "run_status": "demo",
            "is_alive": False,
            "process_id": None,
            "data_time": data_time,
            "observation_mode": False,
            "observation_reason": "",
            **_workflow_calendar_meta(DEMO_RUN_ID, data_time, event_date),
        })
        return graph
    store = _workflow_store_for_run(run_id)
    if not store:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)
    graph = store.build_graph()
    meta = _workflow_runtime_meta(store.run_id)
    meta.update(_workflow_calendar_meta(store.run_id, meta.get("data_time", ""), _workflow_event_display_date(graph)))
    graph.update(meta)
    return graph


@router.get("/api/workflow/runs/{run_id}/artifacts/{event_id}/{name}")
async def api_workflow_artifact(run_id: str, event_id: str, name: str):
    if run_id in {"active", DEMO_RUN_ID} and event_id.startswith("demo_wf_") and not _get_active_output_dir():
        demo_content = {
            "input.json": {"event_id": event_id, "demo": True, "input": ["market", "plan", "risk"]},
            "output.json": {"event_id": event_id, "demo": True, "summary": "这是 Demo artifact，用于展示流程可追踪能力。"},
            "error.txt": "",
            "prompt.txt": "Demo 模式未调用真实 Agent。",
            "response.txt": "Demo 模式返回固定样例。",
        }.get(name)
        if demo_content is None:
            return JSONResponse({"error": "Invalid artifact path"}, status_code=400)
        content = demo_content if isinstance(demo_content, str) else json.dumps(demo_content, ensure_ascii=False, indent=2)
        return {"run_id": DEMO_RUN_ID, "event_id": event_id, "name": name, "content": content}
    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)

    allowed_names = {"input.json", "output.json", "prompt.txt", "response.txt", "error.txt"}
    if event_id in {"", ".", ".."} or "/" in event_id or "\\" in event_id or name not in allowed_names:
        return JSONResponse({"error": "Invalid artifact path"}, status_code=400)

    artifact_root = os.path.abspath(os.path.join(output_dir, "workflow_artifacts"))
    artifact_path = os.path.abspath(os.path.join(artifact_root, event_id, name))
    if not artifact_path.startswith(artifact_root + os.sep):
        return JSONResponse({"error": "Invalid artifact path"}, status_code=400)
    if not os.path.exists(artifact_path):
        return JSONResponse({"error": "Artifact not found"}, status_code=404)

    with open(artifact_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {
        "run_id": os.path.basename(output_dir),
        "event_id": event_id,
        "name": name,
        "content": content,
    }


@router.get("/api/workflow/runs/{run_id}/agent-runs/{task_id}/timeline")
async def api_agent_run_timeline(run_id: str, task_id: str):
    if task_id in {"", ".", ".."} or "/" in task_id or "\\" in task_id:
        return JSONResponse({"error": "Invalid agent task id"}, status_code=400)
    if run_id in {"active", DEMO_RUN_ID} and not _get_active_output_dir():
        return _demo_agent_run_timeline(task_id)

    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)

    agent_root = os.path.abspath(os.path.join(output_dir, "agent_runs"))
    agent_dir = os.path.abspath(os.path.join(agent_root, task_id))
    if not agent_dir.startswith(agent_root + os.sep):
        return JSONResponse({"error": "Invalid agent task id"}, status_code=400)
    if not os.path.isdir(agent_dir):
        return {
            "run_id": os.path.basename(output_dir),
            "task_id": task_id,
            "events": [],
            "tasks": {},
            "warnings": ["agent run not found"],
        }

    audit = validate_agent_events(agent_dir)
    return {
        "run_id": os.path.basename(output_dir),
        "task_id": task_id,
        "events": audit.get("events", []),
        "tasks": audit.get("tasks", {}),
        "warnings": audit.get("warnings", []),
    }


@router.get("/api/workflow/runs/{run_id}/agent-runs/{task_id}/artifacts/{artifact_ref:path}")
async def api_agent_run_artifact(run_id: str, task_id: str, artifact_ref: str):
    if task_id in {"", ".", ".."} or "/" in task_id or "\\" in task_id:
        return JSONResponse({"error": "Invalid agent task id"}, status_code=400)
    if not artifact_ref or artifact_ref in {".", ".."}:
        return JSONResponse({"error": "Invalid agent artifact path"}, status_code=400)
    if run_id in {"active", DEMO_RUN_ID} and not _get_active_output_dir():
        return {
            "run_id": DEMO_RUN_ID,
            "task_id": task_id,
            "artifact_ref": artifact_ref,
            "content": f"Demo artifact: {artifact_ref}",
        }

    output_dir = _get_run_output_dir(run_id)
    if not output_dir:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)

    agent_root = os.path.abspath(os.path.join(output_dir, "agent_runs"))
    agent_dir = os.path.abspath(os.path.join(agent_root, task_id))
    artifact_path = os.path.abspath(os.path.join(agent_dir, artifact_ref))
    if not agent_dir.startswith(agent_root + os.sep) or not artifact_path.startswith(agent_dir + os.sep):
        return JSONResponse({"error": "Invalid agent artifact path"}, status_code=400)
    if not os.path.isfile(artifact_path):
        return JSONResponse({"error": "Agent artifact not found"}, status_code=404)

    with open(artifact_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {
        "run_id": os.path.basename(output_dir),
        "task_id": task_id,
        "artifact_ref": artifact_ref,
        "content": content,
    }


@router.get("/")
async def root():
    """Redirect to dashboard."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/dashboard")


# Dashboard HTML page
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    dashboard_html = os.path.join(DASHBOARD_DIST_DIR, "index.html")
    if not os.path.exists(dashboard_html):
        return HTMLResponse(
            "<h1>Dashboard build not found</h1>"
            "<p>Run <code>npm run dashboard:build</code> before opening /dashboard.</p>",
            status_code=503,
        )
    with open(dashboard_html, "r", encoding="utf-8") as f:
        html = f.read()

    # Inject initial data for zero-request first paint
    output_dir = _get_active_output_dir()
    initial_data = {"has_active_run": False}
    if output_dir:
        state = _read_json(os.path.join(output_dir, "state.json"))
        plan = _read_json(os.path.join(output_dir, "plan.json"))
        if state:
            cash = state.get("cash", 0)
            positions = state.get("holdings", {})
            pv = sum(p.get("shares", 0) * p.get("current_price", 0) for p in positions.values())
            nav = state.get("initial_capital", 100000)
            total = cash + pv
            initial_data = {
                "has_active_run": True,
                "run_id": os.path.basename(output_dir),
                "state": {
                    "total_asset": round(total, 2),
                    "cash": round(cash, 2),
                    "position_value": round(pv, 2),
                    "day_pnl": round(total - nav, 2),
                    "day_return_pct": round((total - nav) / max(nav, 1) * 100, 2),
                },
                "plan_summary": {
                    "market_bias": plan.get("market_bias", "neutral") if plan else "neutral",
                    "bias_confidence": plan.get("bias_confidence", 0) if plan else 0,
                    "candidates": len(plan.get("buy_candidates", [])) if plan else 0,
                } if plan else {},
            }
    else:
        demo_state = _demo_state()
        initial_data = {
            "has_active_run": False,
            "run_id": DEMO_RUN_ID,
            "state": demo_state,
            "plan_summary": _demo_plan(),
            "data_time": demo_state["data_time"],
            "demo": True,
        }
    html = html.replace(
        "window.__DATA__ = {};",
        f"window.__DATA__ = {json.dumps(initial_data, ensure_ascii=False)};",
    )
    return HTMLResponse(html)
