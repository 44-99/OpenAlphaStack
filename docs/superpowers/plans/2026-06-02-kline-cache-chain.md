# Kline Cache Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not commit unless the user explicitly requests it; `CLAUDE.md` forbids automatic commits.

**Goal:** Add a complete Dashboard K-line cache chain for day/week/1m/5m/15m/60m and make the clear-cache button clear all K-line cache levels only.

**Architecture:** Keep K-line cache logic inside `src/alphaclaude/app/main.py` near the Dashboard API. Store new cache under `data/cache/kline`, continue reading legacy `data/cache/minute`, derive week from day and 5/15/60m from 1m, and expose unified cache status/clear routes.

**Tech Stack:** FastAPI, pandas, requests, ECharts, Alpine.js, pytest.

---

### Task 1: Backend K-line Cache Helpers

**Files:**
- Modify: `src/alphaclaude/app/main.py`
- Test: `tests/test_dashboard_cache.py`

- [x] Add constants for `KLINE_CACHE_DIR`, `LEGACY_MINUTE_CACHE_DIR`, day/week/minute cache paths, safe path checks, and JSON DataFrame serialization.
- [x] Implement `_kline_cache_stats()` that totals files in `data/cache/kline` plus legacy `data/cache/minute`.
- [x] Implement `_clear_kline_cache()` that deletes files under only those K-line cache roots.
- [x] Update cache API routes from minute-only to unified K-line cache names while keeping the old POST route as a compatibility alias.

### Task 2: Backend K-line Loading Chain

**Files:**
- Modify: `src/alphaclaude/app/main.py`
- Test: `tests/test_dashboard_cache.py`

- [x] Implement day loading as cache-first JSON with Tencent refresh fallback.
- [x] Implement week loading by resampling day data and caching the result.
- [x] Implement 1m loading as cache-first from new cache, then legacy parquet fallback.
- [x] Implement 5m/15m/60m by resampling 1m and caching the derived result.
- [x] Preserve the API response shape: `code`, `dates`, `open`, `high`, `low`, `close`, `volume`, and add optional `source`.

### Task 3: Dashboard UI Wiring

**Files:**
- Modify: `dashboard/index.html`

- [x] Restore buttons for `1分`, `5分`, `15分`, `60分`.
- [x] Rename cache text from “分钟缓存” to “K线缓存”.
- [x] Change clear button method to call `/api/cache/kline/clear`.
- [x] Show a toast when minute periods have no cached source instead of silently doing nothing.

### Task 4: Verification

**Files:**
- Modify: `tests/test_dashboard_cache.py`

- [x] Update tests for unified K-line cache stats and clear behavior.
- [x] Add tests for resampling 1m to 5m/15m/60m using a temporary cache root.
- [x] Run:
  `python -m py_compile src\alphaclaude\app\main.py src\alphaclaude\app\cli.py`
- [x] Run Dashboard inline JS syntax check with Node.
- [x] Run:
  `$env:TMP=(Resolve-Path 'data\test_tmp').Path; $env:TEMP=$env:TMP; python -m pytest tests\test_dashboard_cache.py -q --basetemp data\test_tmp\pytest_tmp -o cache_dir=data\test_tmp\pytest_cache`
