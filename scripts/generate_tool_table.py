"""Generate CLAUDE.md tool table from @tool_meta annotations.

Scans alphaclaude.tools.* modules, collects registered metadata, and
prints a Markdown table suitable for dropping into CLAUDE.md.

Usage:
    python scripts/generate_tool_table.py
    python scripts/generate_tool_table.py --check  (exit 1 if stale)
"""

from __future__ import annotations

import sys
import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from alphaclaude.tools._registry import scan_tools, get_all_meta

CATEGORY_ORDER = ["行情", "基本面与资金", "信息与筛选", "形态与信号", "回测", "自选股", "风控与交易", "报表"]


def build_tables() -> dict[str, list[dict[str, str]]]:
    """Return metadata grouped by category, ordered by CATEGORY_ORDER."""
    meta = scan_tools()
    groups: dict[str, list[dict[str, str]]] = {}
    for modname, info in meta.items():
        cat = info.get("category", "其他")
        groups.setdefault(cat, []).append(info)
    # Sort each group by name
    for g in groups.values():
        g.sort(key=lambda x: x.get("name", ""))
    return groups


def render_markdown() -> str:
    """Render full tool table markdown."""
    groups = build_tables()
    lines: list[str] = []
    for cat in CATEGORY_ORDER:
        tools = groups.pop(cat, [])
        if not tools:
            continue
        lines.append(f"### {cat}")
        lines.append("")
        lines.append("| 工具 | 调用方式 | 场景 |")
        lines.append("|------|----------|------|")
        for t in tools:
            name = t.get("name", "")
            usage = t.get("usage", "")
            scenario = t.get("scenario", "")
            lines.append(f"| `{name}` | `{usage}` | {scenario} |")
        lines.append("")
    # Remaining uncategorized
    for cat, tools in sorted(groups.items()):
        if not tools:
            continue
        lines.append(f"### {cat}")
        lines.append("")
        lines.append("| 工具 | 调用方式 | 场景 |")
        lines.append("|------|----------|------|")
        for t in tools:
            name = t.get("name", "")
            usage = t.get("usage", "")
            scenario = t.get("scenario", "")
            lines.append(f"| `{name}` | `{usage}` | {scenario} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    check_mode = "--check" in sys.argv
    md = render_markdown()

    if check_mode:
        claude_md_path = os.path.join(PROJECT_DIR, "CLAUDE.md")
        with open(claude_md_path, encoding="utf-8") as f:
            existing = f.read()
        if md not in existing:
            print("CLAUDE.md tool table is stale. Run: python scripts/generate_tool_table.py")
            sys.exit(1)
        print("CLAUDE.md tool table is up to date.")
        return

    print(md)


if __name__ == "__main__":
    main()
