"""Dashboard routes, SSE stream, and K-line cache helpers."""
import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse

from alphaclaude.engine import run_registry
from alphaclaude.engine.workflow_events import WorkflowEventStore
from alphaclaude.paths import DATA_DIR, PROJECT_ROOT

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
KLINE_PERIODS = {"day", "week", "1m", "5m", "15m", "60m"}
MINUTE_PERIODS = {"1m", "5m", "15m", "60m"}
RESAMPLE_RULES = {"5m": "5min", "15m": "15min", "60m": "60min"}

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
    suffix = "json" if period in ("day", "week") else "parquet"
    return os.path.join(KLINE_CACHE_DIR, period, f"{code}.{suffix}")


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
    if df is None or len(df) < min(limit, 60):
        try:
            fetched = _fetch_tencent_day_df(code, max(limit, 260))
            if not fetched.empty:
                df = fetched
                _write_kline_json(path, df)
        except Exception as e:
            logger.warning("Day K-line fetch failed: %s %s", code, e)
    return df.sort_values("time").tail(limit) if df is not None and not df.empty else None


def _load_week_kline_df(code: str, limit: int):
    path = _kline_cache_path("week", code)
    df = _read_kline_json(path)
    if df is None or len(df) < min(limit, 30):
        day_df = _load_day_kline_df(code, max(limit * 7, 260))
        if day_df is None or day_df.empty:
            return None
        df = _resample_ohlcv(day_df, "W")
        if not df.empty:
            _write_kline_json(path, df)
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
    if df is None or len(df) < min(limit, 120):
        try:
            fetched = _fetch_tencent_minute_df(code, max(limit, 800))
            if not fetched.empty:
                df = fetched
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
    if df is None:
        base_limit = max(limit * int(period[:-1]), 800)
        base_df = _load_1m_kline_df(code, base_limit)
        if base_df is None or base_df.empty:
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


def _build_nav_sse(state: dict) -> str:
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
    return f"event: nav\ndata: {json.dumps({'total_asset': round(total, 2), 'cash': round(cash, 2), 'position_value': round(position_value, 2), 'day_pnl': round(pnl, 2), 'day_return_pct': round(pnl / max(nav, 1) * 100, 2), 'positions': positions, 'data_time': state.get('data_time', '')}, ensure_ascii=False)}\n\n"


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
        state_path = os.path.join(output_dir, "state.json") if output_dir else ""
        if state_path:
            state = _read_json(state_path)
            if state:
                last_data_time = state.get("data_time", "")
                yield _build_nav_sse(state)

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
                        yield _build_nav_sse(state)
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
async def api_state():
    output_dir = _get_active_output_dir()
    if not output_dir:
        return JSONResponse({"error": "No active paper run found"}, status_code=404)
    state = _read_json(os.path.join(output_dir, "state.json"))
    if not state:
        return JSONResponse({"error": "state.json not found"}, status_code=404)
    cash = state.get("cash", 0)
    positions = state.get("holdings", {})
    position_value = sum(
        p.get("shares", 0) * p.get("current_price", 0)
        for p in positions.values()
    )
    nav = state.get("initial_capital", 100000)
    total = cash + position_value
    return {
        "run_id": os.path.basename(output_dir),
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


@router.get("/api/plan")
async def api_plan():
    output_dir = _get_active_output_dir()
    if not output_dir:
        return JSONResponse({"error": "No active paper run found"}, status_code=404)
    plan = _read_json(os.path.join(output_dir, "plan.json"))
    if not plan:
        return JSONResponse({"error": "plan.json not found"}, status_code=404)
    return plan


@router.get("/api/ledger")
async def api_ledger(limit: int = 50, code: str = ""):
    output_dir = _get_active_output_dir()
    if not output_dir:
        return JSONResponse({"error": "No active paper run found"}, status_code=404)
    entries = _read_jsonl(os.path.join(output_dir, "ledger.jsonl"), limit=limit)
    if code:
        entries = [e for e in entries if e.get("symbol", "") == code or e.get("code", "") == code]
    return entries


@router.get("/api/quote/{code}")
async def api_quote(code: str):
    try:
        from alphaclaude.tools.quote import get_stock_quote
        result = get_stock_quote(code)
        return result if result else {"error": f"No data for {code}"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/kline/{code}")
async def api_kline(code: str, period: str = "day", limit: int = 200):
    """Return OHLCV data for ECharts candlestick rendering.

    Periods:
      - day: cache-first Tencent daily K-line
      - week: resampled from day K-line
      - 1m: cache-first Tencent minute K-line / legacy parquet fallback
      - 5m/15m/60m: resampled from 1m K-line
    """
    period = period.lower()
    limit = max(1, min(int(limit), 2000))
    if period not in KLINE_PERIODS:
        return JSONResponse({"error": f"Unsupported period: {period}"}, status_code=400)

    try:
        if period == "day":
            df = _load_day_kline_df(code, limit)
        elif period == "week":
            df = _load_week_kline_df(code, limit)
        else:
            df = _load_minute_kline_df(code, period, limit)
    except Exception as e:
        logger.warning("K-line load failed: %s %s %s", code, period, e)
        df = None

    if df is None or df.empty:
        return JSONResponse({"error": f"No K-line data for {code} {period}"}, status_code=404)

    return _df_to_kline_payload(code, df, f"{period}_cache_chain")


@router.get("/api/technical/{code}")
async def api_technical(code: str, indicator: str = "all"):
    try:
        from alphaclaude.tools.technical import get_technical
        result = get_technical(code, indicator=indicator)
        return result if result else {"error": f"No data for {code}"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/watchlist")
async def api_watchlist():
    try:
        pf_path = os.path.join(str(PROJECT_ROOT), "data", "state", "portfolio.json")
        if os.path.exists(pf_path):
            with open(pf_path, "r", encoding="utf-8") as f:
                return json.load(f)
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


@router.get("/api/engine/status")
async def api_engine_status():
    records = run_registry.list_runs("paper")
    record = records[0] if records else None
    output_dir = record.run_dir if record else _get_active_output_dir()
    run_id = record.run_id if record else (os.path.basename(output_dir) if output_dir else None)
    state = _read_json(os.path.join(output_dir, "state.json")) if output_dir else None
    engine_meta = record.engine_meta if record else (state.get("engine_meta", {}) if state else {})
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
    store = _workflow_store_for_run(run_id)
    if not store:
        return JSONResponse({"error": f"Run not found: {run_id}"}, status_code=404)
    safe_limit = max(1, min(int(limit), 2000))
    return {"run_id": store.run_id, "events": store.read_events(limit=safe_limit)}


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


@router.post("/api/terminal/send")
async def api_terminal_send(request: Request):
    """Send a message to the Claude Code agent for processing."""
    try:
        body = await request.json()
        message = body.get("message", "")
        session_id = body.get("session_id", "main")
        if not message.strip():
            return JSONResponse({"error": "Empty message"}, status_code=400)

        # Route to Claude Code for processing
        from alphaclaude.claude import ask_claude
        response = ask_claude(message, session_id=session_id)
        return {"response": response, "session_id": session_id}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


AGENT_PROVIDERS = {"claude", "codex"}


def _build_agent_prompt(message: str, context: dict, provider: str = "claude") -> str:
    """Build a Dashboard Claude Code prompt with current trading context."""
    provider_name = "Codex CLI" if provider == "codex" else "Claude Code"
    context_lines = [
        f"你正在 AlphaClaude Dashboard 右侧 Agent 面板中通过 {provider_name} 协助用户。",
        "回答要直接、可执行。涉及交易计划修改时，先说明建议，不要假装已经改动文件。",
        "",
        "【当前 Dashboard 上下文】",
        f"- 当前股票: {context.get('selected_code', '')}",
        f"- K线周期: {context.get('period', '')}",
        f"- 叠加指标: {context.get('overlay', '')}",
        f"- 引擎状态: {context.get('engine_status', '')}",
        f"- 观察模式: {context.get('observation_mode', False)}",
        f"- 数据时间: {context.get('data_time', '')}",
        f"- 市场方向: {context.get('market_bias', '')}",
        f"- 候选标的: {', '.join(context.get('candidates', []) or [])}",
        "",
        "【用户问题】",
        message,
    ]
    return "\n".join(context_lines)


def _stream_claude_agent(prompt: str, session_id: str):
    """Yield text chunks from Claude Code."""
    from alphaclaude.claude import ask_claude_stream

    emitted = False
    for chunk in ask_claude_stream(prompt, session_id=session_id):
        if chunk:
            emitted = True
            yield chunk
    if not emitted:
        yield "Claude Code 没有返回内容。"


def _extract_codex_text_event(obj: dict) -> str:
    """Extract user-visible text from a Codex JSONL event."""
    for key in ("delta", "text", "message", "content"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    item = obj.get("item")
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        if isinstance(content, str):
            return content
    return ""


def _stream_codex_agent(prompt: str):
    """Yield text chunks from Codex CLI in read-only non-interactive mode."""
    codex_cmd = shutil.which("codex")
    if not codex_cmd:
        yield "Codex CLI 未找到，请确认已安装并在 PATH 中。"
        return

    cmd = [
        codex_cmd,
        "exec",
        "--json",
        "--cd",
        str(PROJECT_ROOT),
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "-",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError) as e:
        yield f"Codex CLI 启动失败: {e}"
        return

    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()

        emitted = False
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                if "WARNING:" not in line:
                    emitted = True
                    yield line + "\n"
                continue
            text = _extract_codex_text_event(obj)
            if text:
                emitted = True
                yield text
        proc.wait(timeout=10)
        if not emitted:
            yield "Codex CLI 没有返回可显示内容。"
    except Exception as e:
        yield f"Codex CLI 调用失败: {e}"
    finally:
        try:
            proc.kill()
        except (OSError, ProcessLookupError):
            pass


@router.post("/api/agent/{provider}/stream")
async def api_agent_stream(provider: str, request: Request):
    """Stream provider output for the Dashboard Agent panel."""
    provider = provider.lower().strip()
    if provider not in AGENT_PROVIDERS:
        return JSONResponse({"error": f"Unsupported agent provider: {provider}"}, status_code=400)
    try:
        body = await request.json()
        message = str(body.get("message", "")).strip()
        default_session = f"dashboard-{provider}-agent"
        session_id = str(body.get("session_id", default_session)).strip() or default_session
        context = body.get("context", {})
        if not isinstance(context, dict):
            context = {}
        if not message:
            return JSONResponse({"error": "Empty message"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    prompt = _build_agent_prompt(message, context, provider)

    def generate():
        try:
            if provider == "codex":
                yield from _stream_codex_agent(prompt)
            else:
                yield from _stream_claude_agent(prompt, session_id)
        except Exception as e:
            yield f"{provider} 调用失败: {e}"

    return StreamingResponse(
        generate(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/api/agent/claude/stream")
async def api_agent_claude_stream_compat(request: Request):
    """Backward-compatible Claude stream route."""
    return await api_agent_stream("claude", request)


def _agent_terminal_command(provider: str) -> str:
    """Return the command auto-entered inside the embedded PowerShell terminal."""
    if provider == "codex":
        codex_cmd = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
        if codex_cmd:
            return f'& "{codex_cmd}" --no-alt-screen'
        return "codex --no-alt-screen"
    claude_cmd = shutil.which("claude.cmd") or shutil.which("claude.exe") or shutil.which("claude")
    if claude_cmd:
        return f'& "{claude_cmd}"'
    return "claude"


def _agent_terminal_startup_args(provider: str) -> str:
    """Build a PowerShell command line that configures UTF-8 without echoing setup text."""
    script = "\n".join(
        [
            "$OutputEncoding=[System.Text.UTF8Encoding]::new()",
            "[Console]::InputEncoding=[System.Text.UTF8Encoding]::new()",
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()",
            "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force",
            "chcp 65001 | Out-Null",
            _agent_terminal_command(provider),
        ]
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"-NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -EncodedCommand {encoded}"


@router.websocket("/api/agent/terminal/{provider}")
async def api_agent_terminal(websocket: WebSocket, provider: str):
    """Attach a browser terminal to local PowerShell and auto-start an agent CLI."""
    provider = provider.lower().strip()
    if provider not in AGENT_PROVIDERS:
        await websocket.close(code=1008, reason=f"Unsupported provider: {provider}")
        return

    await websocket.accept()

    try:
        import winpty
    except Exception as e:
        await websocket.send_text(f"\r\nwinpty 不可用，无法启动内嵌终端: {e}\r\n")
        await websocket.close(code=1011)
        return

    cols = 100
    rows = 30
    try:
        cols = max(20, min(int(websocket.query_params.get("cols", cols)), 240))
        rows = max(8, min(int(websocket.query_params.get("rows", rows)), 80))
    except (TypeError, ValueError):
        pass

    pty = winpty.PTY(cols, rows)
    startup_args = _agent_terminal_startup_args(provider)
    try:
        pty.spawn(f"pwsh.exe {startup_args}")
    except Exception:
        pty.spawn(f"powershell.exe {startup_args}")

    loop = asyncio.get_running_loop()
    output_queue: asyncio.Queue[str | None] = asyncio.Queue()
    stop_event = threading.Event()

    def reader() -> None:
        while not stop_event.is_set():
            try:
                data = pty.read()
            except Exception as e:
                loop.call_soon_threadsafe(output_queue.put_nowait, f"\r\n[terminal closed] {e}\r\n")
                break
            if data:
                loop.call_soon_threadsafe(output_queue.put_nowait, data)
            elif not pty.isalive():
                break
        loop.call_soon_threadsafe(output_queue.put_nowait, None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    async def sender() -> None:
        while True:
            data = await output_queue.get()
            if data is None:
                break
            await websocket.send_text(data)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            if msg_type == "input":
                text = msg.get("data", "")
                if isinstance(text, str):
                    pty.write(text)
            elif msg_type == "resize":
                try:
                    next_cols = max(20, min(int(msg.get("cols", cols)), 240))
                    next_rows = max(8, min(int(msg.get("rows", rows)), 80))
                    pty.set_size(next_cols, next_rows)
                except (TypeError, ValueError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        sender_task.cancel()
        try:
            pty.write("\x03")
            pty.write("exit\r")
        except Exception:
            pass


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
    html = html.replace(
        "window.__DATA__ = {};",
        f"window.__DATA__ = {json.dumps(initial_data, ensure_ascii=False)};",
    )
    return HTMLResponse(html)



