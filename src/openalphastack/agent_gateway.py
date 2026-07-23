"""Safe, provider-neutral gateway used by MCP and future Agent hosts."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from openalphastack.contracts import (
    PLAN_SCHEMA_VERSION,
    RUN_SNAPSHOT_SCHEMA_VERSION,
    normalize_published_plan,
)
from openalphastack.engine import run_registry
from openalphastack.engine.run_store import PlanRevisionConflict, RunStore


_CODE_RE = re.compile(r"^\d{6}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_FINAL_STATUSES = {"completed", "failed", "stopped"}


class GatewayError(ValueError):
    """Raised when an Agent request violates the paper-only contract."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _run_dir(run_id: str, *, paper_only: bool = False) -> Path:
    record = run_registry.get_run(run_id)
    if paper_only and record.mode != "paper":
        raise GatewayError("Agent mutations are restricted to paper runs")
    return Path(record.run_dir)


def list_run_summaries(mode: str = "paper", limit: int = 20) -> list[dict[str, Any]]:
    if mode not in {"paper", "backtest"}:
        raise GatewayError("MCP exposes paper and backtest runs only")
    limit = max(1, min(int(limit), 100))
    return [record.to_dict() for record in run_registry.list_runs(mode)[:limit]]


def get_run_snapshot(run_id: str) -> dict[str, Any]:
    run_dir = _run_dir(run_id)
    stored = RunStore(run_dir).read_snapshot(ledger_limit=100)
    state = stored["state"] or _read_json(run_dir / "state.json")
    plan = stored["plan"] or _read_json(run_dir / "plan.json")
    return {
        "schema_version": RUN_SNAPSHOT_SCHEMA_VERSION,
        "run_id": run_id,
        "state": state,
        "plan": plan,
        "state_revision": stored["state_revision"],
        "plan_revision": stored["plan_revision"],
        "ledger_tail": stored["ledger_tail"] or get_ledger_tail(run_id, limit=100),
    }


def get_ledger_tail(run_id: str, limit: int = 100) -> list[dict[str, Any]]:
    run_dir = _run_dir(run_id)
    limit = max(1, min(int(limit), 1000))
    store = RunStore(run_dir)
    rows = store.read_ledger(limit)
    if rows:
        return rows
    path = run_dir / "ledger.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def validate_paper_plan(plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(plan, dict):
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "valid": False,
            "errors": ["plan must be an object"],
            "warnings": [],
        }

    plan_date = str(plan.get("plan_date") or "")
    try:
        datetime.strptime(plan_date, "%Y-%m-%d")
    except ValueError:
        errors.append("plan_date must use YYYY-MM-DD")

    if plan.get("market_bias", "neutral") not in {"bullish", "neutral", "bearish"}:
        errors.append("market_bias must be bullish, neutral, or bearish")

    try:
        cap = float(plan.get("position_cap_pct", 0))
    except (TypeError, ValueError):
        cap = -1
    if not 0 <= cap <= 100:
        errors.append("position_cap_pct must be between 0 and 100")

    candidates = plan.get("buy_candidates", [])
    if not isinstance(candidates, list):
        errors.append("buy_candidates must be a list")
        candidates = []
    if len(candidates) > 20:
        errors.append("buy_candidates cannot contain more than 20 items")

    total_position = 0.0
    seen: set[str] = set()
    for index, candidate in enumerate(candidates):
        prefix = f"buy_candidates[{index}]"
        if not isinstance(candidate, dict):
            errors.append(f"{prefix} must be an object")
            continue
        code = str(candidate.get("code") or "")
        if not _CODE_RE.fullmatch(code):
            errors.append(f"{prefix}.code must be a six-digit stock code")
        if code in seen:
            errors.append(f"duplicate candidate code: {code}")
        seen.add(code)
        try:
            position = float(candidate.get("position_pct", 0))
        except (TypeError, ValueError):
            position = -1
        if not 0 < position <= 25:
            errors.append(f"{prefix}.position_pct must be greater than 0 and at most 25")
        total_position += max(position, 0)
        try:
            entry = float(candidate.get("entry_max", 0))
        except (TypeError, ValueError):
            entry = 0
        if entry <= 0:
            errors.append(f"{prefix}.entry_max must be positive")

    if total_position > cap + 1e-9:
        errors.append("sum of candidate position_pct exceeds position_cap_pct")
    if not candidates:
        warnings.append("plan has no buy candidates; it will remain observation-only unless it adjusts holdings")

    adjustments = plan.get("holding_adjustments", [])
    if not isinstance(adjustments, list):
        errors.append("holding_adjustments must be a list")

    provenance = plan.get("provenance") or {}
    if isinstance(provenance, dict) and provenance.get("contains_demo_data") is True:
        errors.append("plans derived from Demo data cannot be published")

    if not errors:
        try:
            normalize_published_plan(plan)
        except (TypeError, ValueError, ValidationError) as exc:
            errors.append(f"plan contract validation failed: {exc}")

    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def save_plan_draft(run_id: str, plan: dict[str, Any]) -> dict[str, Any]:
    run_dir = _run_dir(run_id, paper_only=True)
    validation = validate_paper_plan(plan)
    draft = normalize_published_plan(plan) if validation["valid"] else dict(plan)
    draft["schema_version"] = PLAN_SCHEMA_VERSION
    draft["validation"] = validation
    draft["draft_saved_at"] = datetime.now().isoformat(timespec="seconds")
    path = run_dir / "plan.codex-draft.json"
    _atomic_write_json(path, draft)
    return {"run_id": run_id, "path": path.name, **validation}


def publish_paper_plan(
    run_id: str,
    plan: dict[str, Any],
    idempotency_key: str,
    expected_updated: str = "",
) -> dict[str, Any]:
    run_dir = _run_dir(run_id, paper_only=True)
    if not _KEY_RE.fullmatch(idempotency_key):
        raise GatewayError("idempotency_key must be 8-128 safe characters")

    validation = validate_paper_plan(plan)
    if not validation["valid"]:
        return {"published": False, **validation}

    plan_path = run_dir / "plan.json"
    store = RunStore(run_dir)
    current, _revision = store.load_plan()
    if not current:
        current = _read_json(plan_path)
        if current:
            store.save_plan(current)

    published = normalize_published_plan(plan)
    published["updated"] = datetime.now().isoformat(timespec="seconds")
    published["updated_by"] = "codex-skill"
    published["source"] = "openalphastack-mcp"
    published["idempotency_key"] = idempotency_key

    mutation = {
        "idempotency_key": idempotency_key,
        "operation": "publish_paper_plan",
        "run_id": run_id,
        "published_at": published["updated"],
        "plan_date": published.get("plan_date", ""),
    }
    try:
        _revision, replayed, committed_mutation = store.publish_plan(
            published,
            mutation,
            expected_updated=expected_updated,
        )
    except PlanRevisionConflict as exc:
        raise GatewayError("plan changed since it was read; fetch a fresh snapshot and retry") from exc

    if not replayed:
        try:
            _atomic_write_json(plan_path, published)
            mutation_path = run_dir / "agent_mutations.jsonl"
            with mutation_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(mutation, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return {"published": True, "replayed": replayed, "mutation": committed_mutation, **validation}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    last_error: PermissionError | None = None
    for attempt in range(8):
        try:
            os.replace(temp, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(min(0.02 * (2 ** attempt), 0.5))
    try:
        temp.unlink()
    except OSError:
        pass
    if last_error is not None:
        raise last_error
