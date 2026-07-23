"""Local installation diagnostics with no network or trading mutations."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from openalphastack import __version__
from openalphastack.contracts import MCP_SCHEMA_VERSION
from openalphastack.paths import DATA_DIR, PROJECT_ROOT


def _check(name: str, ok: bool, detail: str, required: bool = True) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "required": required, "detail": detail}


def _json_file_check(name: str, path: Path) -> dict[str, Any]:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _check(name, False, f"{path.name}: {type(exc).__name__}")
    return _check(name, True, str(path))


def build_report() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_check("python", sys.version_info >= (3, 10), sys.version.split()[0]))
    checks.append(_check("package", True, f"openalphastack {__version__}"))
    checks.append(_json_file_check("plugin_manifest", PROJECT_ROOT / ".codex-plugin" / "plugin.json"))
    checks.append(_json_file_check("mcp_config", PROJECT_ROOT / ".mcp.json"))

    modules = (("mcp", True), ("pandas", True), ("fastapi", False))
    for module, required in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:
            checks.append(_check(f"import_{module}", False, type(exc).__name__, required=required))
        else:
            checks.append(_check(f"import_{module}", True, "available", required=required))

    skill_names = ("market-analyzer", "stock-screener", "stock-analyzer", "t0-intraday")
    missing = [name for name in skill_names if not (PROJECT_ROOT / "skills" / name / "SKILL.md").is_file()]
    checks.append(_check("domain_skills", not missing, "all present" if not missing else f"missing: {', '.join(missing)}"))

    probe = DATA_DIR / ".doctor-write-test"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        checks.append(_check("data_directory", False, type(exc).__name__))
    else:
        checks.append(_check("data_directory", os.access(DATA_DIR, os.W_OK), str(DATA_DIR)))

    ok = all(item["ok"] for item in checks if item["required"])
    return {"schema_version": MCP_SCHEMA_VERSION, "ok": ok, "checks": checks}


def render_text(report: dict[str, Any]) -> str:
    lines = [f"OpenAlphaStack doctor: {'PASS' if report['ok'] else 'FAIL'}"]
    for item in report["checks"]:
        lines.append(f"[{'ok' if item['ok'] else '!!'}] {item['name']}: {item['detail']}")
    return "\n".join(lines)
