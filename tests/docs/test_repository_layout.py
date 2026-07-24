from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _markdown_files() -> list[Path]:
    files = list(ROOT.glob("*.md"))
    for directory in (ROOT / ".github", ROOT / "deploy", ROOT / "docs", ROOT / "skills"):
        files.extend(directory.rglob("*.md"))
    return sorted(set(files))


def test_repository_metadata_uses_curated_locations():
    canonical = (
        ROOT / ".github" / "CONTRIBUTING.md",
        ROOT / ".github" / "SECURITY.md",
        ROOT / "deploy" / "Dockerfile",
        ROOT / "deploy" / "Dockerfile.public",
        ROOT / "deploy" / "docker-compose.yml",
        ROOT / "deploy" / "requirements.txt",
        ROOT / "docs" / "agent-guide.md",
        ROOT / "docs" / "CHANGELOG.md",
        ROOT / "docs" / "README_EN.md",
        ROOT / "docs" / "site" / "index.html",
    )
    legacy_root_entries = (
        ROOT / "AGENT_GUIDE.md",
        ROOT / "CHANGELOG.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "Dockerfile",
        ROOT / "Dockerfile.public",
        ROOT / "README_EN.md",
        ROOT / "SECURITY.md",
        ROOT / "docker-compose.yml",
        ROOT / "requirements.txt",
        ROOT / "site",
    )

    assert all(path.exists() for path in canonical)
    assert not any(path.exists() for path in legacy_root_entries)


def test_local_markdown_links_resolve():
    missing: list[str] = []

    for path in _markdown_files():
        content = path.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(content):
            target = raw_target.strip().split()[0].strip("<>")
            parsed = urlsplit(target)
            if not target or parsed.scheme or parsed.netloc or target.startswith("#"):
                continue
            local = parsed.path
            if local and not (path.parent / local).resolve().exists():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")

    assert not missing, "Missing Markdown links:\n" + "\n".join(missing)
