"""Shared filesystem paths for package and legacy entrypoints."""

from __future__ import annotations

from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
TOOLS_DIR = PROJECT_ROOT / "tools"


def add_legacy_paths() -> None:
    """Expose project-root and tools modules for compatibility wrappers.

    The repository still contains root-level modules such as `config.py` and
    CLI-oriented modules under `tools/`. During the migration, wrappers call
    this before importing legacy code.
    """
    import sys

    for path in (PROJECT_ROOT, TOOLS_DIR):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
