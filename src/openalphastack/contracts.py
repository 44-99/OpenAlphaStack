"""Versioned public contracts shared by MCP tools and offline checks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator


MCP_SCHEMA_VERSION = "openalphastack.mcp/v1"
PLAN_SCHEMA_VERSION = "openalphastack.plan/v1"
RUN_SNAPSHOT_SCHEMA_VERSION = "openalphastack.run-snapshot/v1"


class PlanRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_single_position_pct: float = Field(default=25.0, gt=0, le=25)
    max_total_position_pct: float = Field(default=80.0, ge=0, le=100)
    min_cash_reserve: float = Field(default=0.0, ge=0)
    stop_loss_mode: str = "hard"
    daily_new_positions_limit: int = Field(default=3, ge=0, le=20)


class PlanCandidate(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str = Field(pattern=r"^\d{6}$")
    entry_max: float = Field(gt=0)
    position_pct: float = Field(gt=0, le=25)
    stop_loss: float | None = None
    stop_loss_pct: float | None = None
    take_profit: float | None = None
    take_profit_pct: float | None = None


class PlanProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_run_id: str = ""
    snapshot_ids: list[str] = Field(default_factory=list)
    latest_data_as_of: str = ""
    quality_status: str = "unknown"
    contains_demo_data: bool = False


class PublishedPlan(BaseModel):
    """The only plan shape admitted to deterministic execution."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = PLAN_SCHEMA_VERSION
    status: str = "active"
    plan_date: str
    market_bias: str = Field(default="neutral", pattern=r"^(bullish|neutral|bearish)$")
    # These fields are audit/display metadata. Invalid model-authored values are
    # normalized rather than allowed to block an otherwise executable plan.
    bias_confidence: int = 50
    bias_reasoning: str = ""
    position_cap_pct: float = Field(default=80.0, ge=0, le=100)
    rules: PlanRules = Field(default_factory=PlanRules)
    provenance: PlanProvenance = Field(default_factory=PlanProvenance)
    buy_candidates: list[PlanCandidate] = Field(default_factory=list, max_length=20)
    holding_adjustments: list[dict[str, Any]] = Field(default_factory=list)
    holdings: dict[str, Any] = Field(default_factory=dict)
    watchlist: list[str] = Field(default_factory=list)
    checklist: list[Any] = Field(default_factory=list)
    pending_orders: list[dict[str, Any]] = Field(default_factory=list)
    preferred_sectors: list[str] = Field(default_factory=list)
    avoid_sectors: list[str] = Field(default_factory=list)
    emergency_triggers: dict[str, Any] = Field(
        default_factory=lambda: {
            "market_drop_pct": 3.0,
            "single_stock_drop_pct": 5.0,
            "account_drawdown_pct": 10.0,
        }
    )
    risk_report: dict[str, Any] = Field(
        default_factory=lambda: {"rejected_candidates": [], "correlation_matrix": {}}
    )
    cooldown: dict[str, Any] = Field(default_factory=dict)
    today_stopped_out: list[str] = Field(default_factory=list)
    emergency_tiers: dict[str, Any] = Field(default_factory=lambda: {"date": "", "tiers": {}})

    @field_validator("bias_confidence", mode="before")
    @classmethod
    def normalize_confidence_metadata(cls, value: Any) -> int:
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, parsed))

    @field_validator("bias_reasoning", mode="before")
    @classmethod
    def normalize_reasoning_metadata(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator("risk_report", mode="before")
    @classmethod
    def normalize_risk_report_metadata(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {"unstructured": value}


def normalize_published_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and hydrate a plan into the exact Engine-facing contract."""
    source = dict(payload)
    raw_rules = dict(source.get("rules") or {})
    cap = float(source.get("position_cap_pct", raw_rules.get("max_total_position_pct", 80.0)))
    raw_rules["max_total_position_pct"] = cap
    raw_rules.setdefault("max_single_position_pct", min(25.0, cap) if cap > 0 else 25.0)
    source["rules"] = raw_rules
    source["schema_version"] = PLAN_SCHEMA_VERSION
    model = PublishedPlan.model_validate(source)
    return model.model_dump(mode="json")


PLAN_JSON_SCHEMA: dict[str, Any] = PublishedPlan.model_json_schema()
PLAN_JSON_SCHEMA["$id"] = PLAN_SCHEMA_VERSION

RUN_SNAPSHOT_JSON_SCHEMA: dict[str, Any] = {
    "$id": RUN_SNAPSHOT_SCHEMA_VERSION,
    "type": "object",
    "required": ["schema_version", "run_id", "state", "plan", "ledger_tail"],
    "properties": {
        "schema_version": {"const": RUN_SNAPSHOT_SCHEMA_VERSION},
        "run_id": {"type": "string"},
        "state": {"type": "object"},
        "plan": {"type": "object"},
        "ledger_tail": {"type": "array"},
    },
}


def contract_catalog() -> dict[str, Any]:
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "contracts": {
            PLAN_SCHEMA_VERSION: PLAN_JSON_SCHEMA,
            RUN_SNAPSHOT_SCHEMA_VERSION: RUN_SNAPSHOT_JSON_SCHEMA,
        },
    }


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for parser in (
        datetime.fromisoformat,
        lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M:%S"),
        lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M"),
        lambda item: datetime.strptime(item, "%Y-%m-%d"),
    ):
        try:
            parsed = parser(text)
            return parsed.astimezone() if parsed.tzinfo else parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        except (TypeError, ValueError):
            continue
    return None


def _infer_metadata(data: Any) -> tuple[str, str]:
    if not isinstance(data, dict):
        return "openalphastack", ""
    source = str(data.get("source") or data.get("provider") or "openalphastack")
    as_of = str(data.get("fetched_at") or data.get("time") or data.get("as_of") or "")
    return source, as_of


def success(
    tool: str,
    data: Any,
    *,
    source: str = "",
    as_of: str = "",
    max_age_seconds: int | None = None,
    demo: bool = False,
) -> dict[str, Any]:
    inferred_source, inferred_as_of = _infer_metadata(data)
    source = source or inferred_source
    as_of = as_of or inferred_as_of
    parsed = _parse_time(as_of)
    age_seconds = None
    if parsed is not None:
        age_seconds = max(0, int((datetime.now().astimezone() - parsed).total_seconds()))
    if demo:
        freshness_status = "static-demo"
    elif age_seconds is None or max_age_seconds is None:
        freshness_status = "unknown"
    else:
        freshness_status = "fresh" if age_seconds <= max_age_seconds else "stale"
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "ok": True,
        "data": data,
        "meta": {
            "tool": tool,
            "source": source,
            "as_of": as_of or None,
            "freshness": {
                "status": freshness_status,
                "age_seconds": age_seconds,
                "max_age_seconds": max_age_seconds,
            },
            "demo": demo,
        },
    }


def failure(
    tool: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        },
        "meta": {"tool": tool},
    }


def call(
    tool: str,
    operation: Callable[[], Any],
    *,
    max_age_seconds: int | None = None,
    source: str = "",
) -> dict[str, Any]:
    """Run a provider/tool call without leaking provider exception text."""
    try:
        data = operation()
    except (TypeError, ValueError) as exc:
        return failure(tool, "INVALID_ARGUMENT", str(exc), details={"exception": type(exc).__name__})
    except Exception as exc:  # provider boundaries are intentionally normalized
        return failure(
            tool,
            "PROVIDER_UNAVAILABLE",
            "The requested data or operation is currently unavailable.",
            retryable=True,
            details={"exception": type(exc).__name__},
        )
    if isinstance(data, dict) and data.get("error"):
        return failure(
            tool,
            "DATA_UNAVAILABLE",
            "The requested data is unavailable from the configured providers.",
            retryable=True,
            details={key: data[key] for key in ("code", "strategy") if key in data},
        )
    return success(tool, data, max_age_seconds=max_age_seconds, source=source)
